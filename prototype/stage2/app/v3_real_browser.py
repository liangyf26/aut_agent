from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from prototype.stage2.app.v3_orchestrator import V3RunConfig


V3_SCHEMA_VERSION = "stage2_v3_run.v1"


async def collect_real_browser_artifacts(config: V3RunConfig, run_dir: Path) -> dict[str, Any]:
    """Collect low-risk evidence from Chrome DevTools Protocol without extra deps."""

    if not config.cdp_url:
        return _blocked("cdp_required", "真实浏览器模式需要 CDP 地址，例如 http://localhost:9222。")
    try:
        return await _collect_with_raw_cdp(config, run_dir)
    except Exception as exc:
        return _blocked(
            "cdp_connect_failed",
            f"真实浏览器执行失败：{type(exc).__name__}: {exc}",
            cdp_url=config.cdp_url,
        )


async def _collect_with_raw_cdp(config: V3RunConfig, run_dir: Path) -> dict[str, Any]:
    screenshots_dir = run_dir / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    ws_url = _resolve_page_websocket(config.cdp_url, config.start_url)
    session = await RawCdpSession.connect(ws_url)
    try:
        await session.call("Page.enable")
        await session.call("Runtime.enable")
        if config.start_url:
            await session.call("Page.navigate", {"url": config.start_url})
            await asyncio.sleep(1.2)
        discovered = await _collect_dom_cdp(session, config.max_pages, config.max_features_per_page)
        screenshot = await _capture_cdp(session, screenshots_dir, "home_visible", "首页可见状态")
        if _looks_like_login_payload(discovered):
            return {
                "schema_version": V3_SCHEMA_VERSION,
                "status": "blocked",
                "failure_reason": "login_required",
                "message": "目标页面看起来仍在登录或接管页，请先在浏览器中完成人工登录后重试。",
                "preflight_result": _preflight(False, "login_required", config.cdp_url),
                "pages": [],
                "features": [],
                "screenshots_index": _screenshots_index([screenshot]),
            }
        pages = _build_pages(
            config,
            discovered.get("url") or config.start_url,
            discovered.get("title") or config.target_name,
            discovered,
            screenshot["screenshot_id"],
        )
        features = _build_features(pages, discovered, config.max_features_per_page, screenshot["screenshot_id"])
        return {
            "schema_version": V3_SCHEMA_VERSION,
            "status": "completed",
            "failure_reason": None,
            "message": "已通过 Chrome DevTools Protocol 完成低风险页面可见元素扫描。",
            "preflight_result": _preflight(True, "ok", config.cdp_url),
            "pages": pages,
            "features": features,
            "screenshots_index": _screenshots_index([screenshot]),
        }
    finally:
        await session.close()


class RawCdpSession:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.reader = reader
        self.writer = writer
        self.next_id = 1

    @classmethod
    async def connect(cls, websocket_url: str) -> "RawCdpSession":
        parsed = urlparse(websocket_url)
        if parsed.scheme != "ws":
            raise ValueError(f"unsupported websocket scheme: {parsed.scheme}")
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.hostname, parsed.port or 80),
            timeout=8,
        )
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {parsed.netloc}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        writer.write(request.encode("ascii"))
        await writer.drain()
        response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=8)
        status_line = response.split(b"\r\n", 1)[0]
        if b" 101 " not in status_line:
            raise ConnectionError(status_line.decode("latin1", errors="replace"))
        return cls(reader, writer)

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = self.next_id
        self.next_id += 1
        await self._send_json({"id": message_id, "method": method, "params": params or {}})
        while True:
            payload = await self._recv_json()
            if payload.get("id") == message_id:
                if "error" in payload:
                    raise RuntimeError(payload["error"])
                return payload.get("result", {})

    async def close(self) -> None:
        with suppress(Exception):
            self.writer.close()
            await self.writer.wait_closed()

    async def _send_json(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        mask = os.urandom(4)
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.extend([0x80 | 126, *struct.pack("!H", length)])
        else:
            header.extend([0x80 | 127, *struct.pack("!Q", length)])
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.writer.write(bytes(header) + mask + masked)
        await self.writer.drain()

    async def _recv_json(self) -> dict[str, Any]:
        while True:
            first = await self.reader.readexactly(2)
            opcode = first[0] & 0x0F
            length = first[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", await self.reader.readexactly(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", await self.reader.readexactly(8))[0]
            if first[1] & 0x80:
                mask = await self.reader.readexactly(4)
                data = await self.reader.readexactly(length)
                data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
            else:
                data = await self.reader.readexactly(length)
            if opcode == 0x8:
                raise ConnectionError("CDP websocket closed")
            if opcode == 0x9:
                continue
            if opcode == 0x1:
                return json.loads(data.decode("utf-8"))


def _resolve_page_websocket(cdp_url: str, start_url: str) -> str:
    base = cdp_url.rstrip("/") + "/"
    try:
        targets = _http_json(urljoin(base, "json/list"))
    except URLError as exc:
        raise ConnectionError(f"CDP 地址不可访问：{cdp_url}；{exc}") from exc
    pages = [item for item in targets if item.get("type") == "page" and item.get("webSocketDebuggerUrl")]
    if pages:
        return pages[0]["webSocketDebuggerUrl"]
    if start_url:
        request = Request(urljoin(base, f"json/new?{quote(start_url, safe=':/?&=%')}"), method="PUT")
        target = _http_json(request)
        if target.get("webSocketDebuggerUrl"):
            return target["webSocketDebuggerUrl"]
    raise ConnectionError("CDP 未返回可连接的 page target。")


def _http_json(url_or_request: str | Request) -> Any:
    with urlopen(url_or_request, timeout=8) as response:
        return json.loads(response.read().decode("utf-8"))


async def _capture_cdp(
    session: RawCdpSession,
    screenshots_dir: Path,
    screenshot_id: str,
    label: str,
) -> dict[str, Any]:
    result = await session.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
    path = screenshots_dir / f"{screenshot_id}.png"
    path.write_bytes(base64.b64decode(result["data"]))
    return {
        "screenshot_id": screenshot_id,
        "label": label,
        "path": str(path),
        "relative_path": str(path.relative_to(screenshots_dir.parent)),
    }


async def _collect_dom_cdp(
    session: RawCdpSession,
    max_pages: int,
    max_features_per_page: int,
) -> dict[str, Any]:
    result = await session.call(
        "Runtime.evaluate",
        {
            "expression": f"({_dom_collection_expression()})({json.dumps({'maxPages': max_pages, 'maxFeatures': max_features_per_page})})",
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    return result.get("result", {}).get("value") or {}


def _looks_like_login_payload(payload: dict[str, Any]) -> bool:
    return bool(payload.get("password") or payload.get("hasLoginWord"))


def _dom_collection_expression() -> str:
    return """(opts) => {
      const maxPages = Math.max(1, Number(opts.maxPages || 1));
      const maxFeatures = Math.max(1, Number(opts.maxFeatures || 1));
      const visible = (el) => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const label = (el) => (
        el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') ||
        el.getAttribute('placeholder') || el.value || el.name || el.id || el.tagName
      ).trim().replace(/\\s+/g, ' ').slice(0, 80);
      const bodyText = document.body ? document.body.innerText.toLowerCase() : '';
      const loginWords = ['login', 'sign in', '登录', '登陆', '验证码'];
      const links = Array.from(document.querySelectorAll('a[href]'))
        .filter(visible)
        .slice(0, maxPages)
        .map((el) => ({ text: label(el), href: el.href }));
      const controls = Array.from(document.querySelectorAll('a[href],button,input,select,textarea,[role="button"],[onclick]'))
        .filter(visible)
        .slice(0, maxFeatures)
        .map((el) => ({
          text: label(el),
          tag: el.tagName.toLowerCase(),
          role: el.getAttribute('role') || '',
          type: el.getAttribute('type') || '',
          href: el.href || '',
          name: el.name || '',
          id: el.id || ''
        }));
      return {
        links,
        controls,
        title: document.title,
        url: location.href,
        password: Boolean(document.querySelector('input[type="password"]')),
        hasLoginWord: loginWords.some((word) => bodyText.includes(word))
      };
    }"""


def _build_pages(
    config: V3RunConfig,
    current_url: str,
    title: str,
    discovered: dict[str, Any],
    screenshot_id: str,
) -> list[dict[str, Any]]:
    pages = [
        {
            "page_id": "page_001",
            "page_entry_id": "page_001",
            "name": title or config.target_name or "首页",
            "url": current_url or config.start_url,
            "source": "real_browser_cdp",
            "confidence": "observed",
            "semantic_page_type": _infer_page_type(title, current_url),
            "priority": "high",
            "requires_human_review": False,
            "evidence": {"screenshot_id": screenshot_id},
        }
    ]
    for index, link in enumerate(discovered.get("links", []), start=2):
        href = str(link.get("href") or "")
        if not href:
            continue
        pages.append(
            {
                "page_id": f"page_{index:03d}",
                "page_entry_id": f"page_{index:03d}",
                "name": str(link.get("text") or f"页面入口 {index}"),
                "url": href,
                "source": "real_browser_visible_link",
                "confidence": "candidate",
                "semantic_page_type": _infer_page_type(str(link.get("text") or ""), href),
                "priority": "normal",
                "requires_human_review": True,
                "evidence": {"screenshot_id": screenshot_id},
            }
        )
    return pages[: max(1, int(config.max_pages or 1))]


def _build_features(
    pages: list[dict[str, Any]],
    discovered: dict[str, Any],
    max_features_per_page: int,
    screenshot_id: str,
) -> list[dict[str, Any]]:
    page_id = pages[0]["page_id"] if pages else "page_001"
    features = []
    for index, control in enumerate(discovered.get("controls", []), start=1):
        text = str(control.get("text") or f"可见控件 {index}")
        feature_type = _infer_feature_type(text, str(control.get("tag") or ""), str(control.get("type") or ""))
        features.append(
            {
                "feature_id": f"feature_{index:03d}",
                "feature_point_id": f"feature_{index:03d}",
                "page_id": page_id,
                "page_entry_id": page_id,
                "name": text,
                "feature_type": feature_type,
                "source": "real_browser_visible_control",
                "confidence": "observed",
                "risk_level": _risk_level(feature_type),
                "requires_test_data": feature_type in {"create", "edit", "submit"},
                "evidence": {"screenshot_id": screenshot_id, "tag": control.get("tag"), "type": control.get("type")},
            }
        )
    if not features:
        features.append(
            {
                "feature_id": "feature_001",
                "feature_point_id": "feature_001",
                "page_id": page_id,
                "page_entry_id": page_id,
                "name": "页面可见性验证",
                "feature_type": "view",
                "source": "real_browser_page_visible",
                "confidence": "observed",
                "risk_level": "low",
                "requires_test_data": False,
                "evidence": {"screenshot_id": screenshot_id},
            }
        )
    return features[: max(1, int(max_features_per_page or 1))]


def _preflight(ok: bool, reason: str, cdp_url: str) -> dict[str, Any]:
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "execution_mode": "real_browser",
        "ok": ok,
        "status": "completed" if ok else "blocked",
        "failure_reason": None if ok else reason,
        "checks": {
            "cdp_url": {"ok": bool(cdp_url), "url": cdp_url},
            "browser_session": {"ok": ok},
            "raw_cdp": {"ok": ok or reason != "cdp_connect_failed"},
        },
    }


def _screenshots_index(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "screenshots": items,
        "items": items,
        "notes": ["截图由 Chrome DevTools Protocol 执行器采集。"] if items else [],
    }


def _blocked(reason: str, message: str, *, cdp_url: str = "") -> dict[str, Any]:
    return {
        "schema_version": V3_SCHEMA_VERSION,
        "status": "blocked",
        "failure_reason": reason,
        "message": message,
        "preflight_result": _preflight(False, reason, cdp_url),
        "pages": [],
        "features": [],
        "screenshots_index": _screenshots_index([]),
    }


def _infer_page_type(name: str, url: str) -> str:
    text = f"{name} {url}".lower()
    if any(keyword in text for keyword in ("query", "search", "list", "查询", "列表")):
        return "query_list"
    if any(keyword in text for keyword in ("detail", "详情")):
        return "detail"
    return "page"


def _infer_feature_type(text: str, tag: str, input_type: str) -> str:
    lowered = f"{text} {tag} {input_type}".lower()
    if any(word in lowered for word in ("delete", "删除", "作废")):
        return "delete"
    if any(word in lowered for word in ("submit", "approve", "提交", "审批", "保存")):
        return "submit"
    if any(word in lowered for word in ("add", "new", "create", "新增", "新建")):
        return "create"
    if any(word in lowered for word in ("edit", "修改", "编辑")):
        return "edit"
    if any(word in lowered for word in ("search", "query", "查询", "检索")) or input_type in {"search", "text"}:
        return "query"
    if tag == "a":
        return "navigation"
    return "view"


def _risk_level(feature_type: str) -> str:
    if feature_type in {"create", "edit", "delete", "submit"}:
        return "high"
    return "low"
