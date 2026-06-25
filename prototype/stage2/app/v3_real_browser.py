from __future__ import annotations

import asyncio
import base64
import json
import os
import struct
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote, urljoin, urlparse
from urllib.request import Request, urlopen

from prototype.stage2.app.v3_orchestrator import V3RunConfig


V3_SCHEMA_VERSION = "stage2_v3_run.v1"
LOW_RISK_ONLY_POLICY = "low_risk_only"
TEST_ENV_FULL_ACCESS_POLICY = "test_env_full_access"
SIDE_EFFECT_ACTION_TYPES = {"submit", "save", "approve", "delete", "create", "edit"}
DEFAULT_SIDE_EFFECT_TOTAL_LIMIT = 4
DEFAULT_SIDE_EFFECT_PER_TYPE_LIMIT = 1


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
        screenshots = [
            await _capture_cdp(session, screenshots_dir, "home_visible", "首页可见状态")
        ]
        if _looks_like_login_payload(discovered):
            return {
                "schema_version": V3_SCHEMA_VERSION,
                "status": "blocked",
                "failure_reason": "login_required",
                "message": "目标页面看起来仍在登录或接管页，请先在浏览器中完成人工登录后重试。",
                "preflight_result": _preflight(False, "login_required", config.cdp_url),
                "pages": [],
                "features": [],
                "screenshots_index": _screenshots_index(screenshots),
            }
        pages = _build_pages(
            config,
            discovered.get("url") or config.start_url,
            discovered.get("title") or config.target_name,
            discovered,
            screenshots[0]["screenshot_id"],
        )
        features = _build_features(
            pages,
            discovered,
            config.max_features_per_page,
            screenshots[0]["screenshot_id"],
        )
        side_effect_bundle = await _execute_side_effect_actions_cdp(
            session,
            screenshots_dir,
            discovered,
            config,
        )
        screenshots.extend(side_effect_bundle["screenshots"])
        return {
            "schema_version": V3_SCHEMA_VERSION,
            "status": "completed",
            "failure_reason": None,
            "message": _real_browser_completion_message(side_effect_bundle),
            "preflight_result": _preflight(True, "ok", config.cdp_url),
            "pages": pages,
            "features": features,
            "screenshots_index": _screenshots_index(screenshots),
            "side_effect_policy": side_effect_bundle["policy"],
            "side_effect_results": side_effect_bundle["results"],
            "side_effect_skipped": side_effect_bundle["skipped"],
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


async def _execute_side_effect_actions_cdp(
    session: RawCdpSession,
    screenshots_dir: Path,
    discovered: dict[str, Any],
    config: V3RunConfig,
) -> dict[str, Any]:
    plan = _plan_side_effect_actions(discovered, config)
    screenshots: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for action in plan["selected"]:
        started_at = _now()
        before_state = await _page_state_cdp(session)
        before_ref = None
        after_ref = None
        try:
            before = await _capture_cdp(
                session,
                screenshots_dir,
                f"side_effect_{action['execution_order']:03d}_before",
                f"副作用动作前：{action['control_label']}",
            )
            before_ref = before["screenshot_id"]
            screenshots.append(before)
            click_result = await _click_side_effect_control_cdp(session, action)
            await asyncio.sleep(1.0)
            after_state = await _page_state_cdp(session)
            after = await _capture_cdp(
                session,
                screenshots_dir,
                f"side_effect_{action['execution_order']:03d}_after",
                f"副作用动作后：{action['control_label']}",
            )
            after_ref = after["screenshot_id"]
            screenshots.append(after)
            clicked = bool(click_result.get("clicked"))
            results.append(
                _side_effect_execution_result(
                    action,
                    status="side_effect_executed" if clicked else "side_effect_failed",
                    started_at=started_at,
                    before_ref=before_ref,
                    after_ref=after_ref,
                    before_state=before_state,
                    after_state=after_state,
                    click_result=click_result,
                    failure_reason=None if clicked else _text(click_result.get("reason")) or "click_failed",
                )
            )
        except Exception as exc:
            after_state = await _safe_page_state_cdp(session)
            results.append(
                _side_effect_execution_result(
                    action,
                    status="side_effect_failed",
                    started_at=started_at,
                    before_ref=before_ref,
                    after_ref=after_ref,
                    before_state=before_state,
                    after_state=after_state,
                    click_result={},
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
            )
    return {
        "policy": plan["policy"],
        "selected": plan["selected"],
        "skipped": plan["skipped"],
        "results": results,
        "screenshots": screenshots,
    }


async def _page_state_cdp(session: RawCdpSession) -> dict[str, Any]:
    result = await session.call(
        "Runtime.evaluate",
        {
            "expression": """(() => {
              const text = document.body ? document.body.innerText : '';
              const dialogLog = Array.isArray(window.__stage2DialogLog) ? window.__stage2DialogLog : [];
              return {
                url: location.href,
                title: document.title,
                visible_text_sample: text.replace(/\\s+/g, ' ').slice(0, 600),
                dialog_events: dialogLog.slice(-10)
              };
            })()""",
            "returnByValue": True,
        },
    )
    return result.get("result", {}).get("value") or {}


async def _safe_page_state_cdp(session: RawCdpSession) -> dict[str, Any]:
    with suppress(Exception):
        return await _page_state_cdp(session)
    return {}


async def _click_side_effect_control_cdp(
    session: RawCdpSession,
    action: dict[str, Any],
) -> dict[str, Any]:
    result = await session.call(
        "Runtime.evaluate",
        {
            "expression": f"({_side_effect_click_expression()})({json.dumps(action)})",
            "returnByValue": True,
            "awaitPromise": True,
        },
    )
    return result.get("result", {}).get("value") or {}


def _plan_side_effect_actions(
    discovered: dict[str, Any],
    config: V3RunConfig,
) -> dict[str, Any]:
    policy = _side_effect_policy(config)
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    per_type_counts: dict[str, int] = {}
    for fallback_index, control in enumerate(discovered.get("controls", []) or []):
        if not isinstance(control, dict):
            continue
        action_type = _normalize_action_type(
            _infer_feature_type(
                str(control.get("text") or ""),
                str(control.get("tag") or ""),
                str(control.get("type") or ""),
            )
        )
        if action_type not in SIDE_EFFECT_ACTION_TYPES:
            continue
        control_label = str(control.get("text") or f"可见控件 {fallback_index + 1}")
        base = {
            "action_id": f"side_effect_{fallback_index + 1:03d}_{action_type}",
            "action_type": action_type,
            "control_label": control_label,
            "risk_level": "high",
            "candidate_index": int(control.get("candidate_index", fallback_index) or 0),
            "control": {
                "text": control_label,
                "tag": str(control.get("tag") or ""),
                "type": str(control.get("type") or ""),
                "role": str(control.get("role") or ""),
                "href": str(control.get("href") or ""),
                "name": str(control.get("name") or ""),
                "id": str(control.get("id") or ""),
            },
        }
        decision = _side_effect_policy_decision(action_type, policy)
        if decision["decision"] == "allowed":
            if len(selected) >= policy["max_total_actions"]:
                decision = {
                    **decision,
                    "decision": "skipped",
                    "reason_code": "side_effect_total_limit_reached",
                }
            elif per_type_counts.get(action_type, 0) >= policy["max_per_action_type"]:
                decision = {
                    **decision,
                    "decision": "skipped",
                    "reason_code": "side_effect_per_type_limit_reached",
                }
        item = {**base, "policy_decision": decision}
        if decision["decision"] == "allowed":
            per_type_counts[action_type] = per_type_counts.get(action_type, 0) + 1
            item["execution_order"] = len(selected) + 1
            selected.append(item)
        else:
            skipped.append(item)
    return {"policy": policy, "selected": selected, "skipped": skipped}


def _side_effect_policy(config: V3RunConfig) -> dict[str, Any]:
    metadata = config.metadata if isinstance(config.metadata, dict) else {}
    safety_policy = _text(
        metadata.get("safety_policy")
        or metadata.get("stage2_safety_policy")
        or config.safety_policy
        or LOW_RISK_ONLY_POLICY
    )
    allowed = _normalize_allowed_side_effect_actions(
        metadata.get("allowed_side_effect_actions")
        or metadata.get("side_effect_allowlist")
        or metadata.get("allowed_side_effects")
        or config.allowed_side_effect_actions
        or config.risk_whitelist
    )
    max_total = _bounded_int(
        metadata.get("max_side_effect_actions"),
        default=DEFAULT_SIDE_EFFECT_TOTAL_LIMIT,
        minimum=0,
        maximum=5,
    )
    max_per_type = _bounded_int(
        metadata.get("max_side_effect_actions_per_type"),
        default=DEFAULT_SIDE_EFFECT_PER_TYPE_LIMIT,
        minimum=0,
        maximum=2,
    )
    return {
        "safety_policy": safety_policy,
        "allowed_side_effect_actions": sorted(allowed),
        "max_total_actions": max_total,
        "max_per_action_type": max_per_type,
        "execution_boundary": (
            "Only visible current-page controls are eligible. Actions are capped and audited with before/after screenshots."
        ),
    }


def _side_effect_policy_decision(action_type: str, policy: dict[str, Any]) -> dict[str, Any]:
    allowed_actions = set(policy.get("allowed_side_effect_actions") or [])
    if policy.get("safety_policy") != TEST_ENV_FULL_ACCESS_POLICY:
        return {
            "decision": "blocked",
            "reason_code": "requires_test_env_full_access",
            "safety_policy": policy.get("safety_policy"),
            "allowed": False,
        }
    if "*" not in allowed_actions and action_type not in allowed_actions:
        return {
            "decision": "blocked",
            "reason_code": "action_not_allowlisted",
            "safety_policy": policy.get("safety_policy"),
            "allowed": False,
        }
    return {
        "decision": "allowed",
        "reason_code": "test_env_full_access_allowlisted",
        "safety_policy": policy.get("safety_policy"),
        "allowed": True,
    }


def _normalize_allowed_side_effect_actions(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        raw_items = [item.strip() for item in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        raw_items = [str(item).strip() for item in value]
    else:
        raw_items = [str(value).strip()]
    normalized = {_normalize_action_type(item) for item in raw_items if item}
    if "all" in normalized or "*" in raw_items:
        return {"*"}
    return {item for item in normalized if item in SIDE_EFFECT_ACTION_TYPES}


def _normalize_action_type(value: str) -> str:
    lowered = _text(value).lower()
    aliases = {
        "approval": "approve",
        "audit": "approve",
        "confirm": "submit",
        "remove": "delete",
        "new": "create",
        "add": "create",
        "update": "edit",
    }
    return aliases.get(lowered, lowered)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _side_effect_execution_result(
    action: dict[str, Any],
    *,
    status: str,
    started_at: str,
    before_ref: str | None,
    after_ref: str | None,
    before_state: dict[str, Any],
    after_state: dict[str, Any],
    click_result: dict[str, Any],
    failure_reason: str | None,
) -> dict[str, Any]:
    return {
        "result_id": f"{action['action_id']}_result",
        "action_id": action["action_id"],
        "action_type": action["action_type"],
        "control_label": action["control_label"],
        "risk_level": action["risk_level"],
        "policy_decision": action["policy_decision"],
        "before_screenshot_ref": before_ref,
        "after_screenshot_ref": after_ref,
        "status": status,
        "failure_reason": failure_reason,
        "started_at": started_at,
        "finished_at": _now(),
        "url_before": before_state.get("url") or click_result.get("url_before"),
        "url_after": after_state.get("url") or click_result.get("url_after"),
        "visible_feedback": after_state.get("visible_text_sample") or "",
        "dialog_events": (click_result.get("dialog_events") or []) + (after_state.get("dialog_events") or []),
        "evidence": [item for item in (before_ref, after_ref) if item],
    }


def _real_browser_completion_message(side_effect_bundle: dict[str, Any]) -> str:
    executed = len(side_effect_bundle.get("results") or [])
    policy = side_effect_bundle.get("policy") or {}
    if executed:
        return f"已通过 Chrome DevTools Protocol 完成页面扫描，并在 {policy.get('safety_policy')} 策略下执行 {executed} 个白名单副作用动作。"
    if policy.get("safety_policy") == TEST_ENV_FULL_ACCESS_POLICY:
        return "已通过 Chrome DevTools Protocol 完成页面扫描；未发现符合白名单和执行上限的可见副作用动作。"
    return "已通过 Chrome DevTools Protocol 完成低风险页面可见元素扫描，默认未执行提交、删除、审批等副作用动作。"


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
      const visibleControls = Array.from(document.querySelectorAll('a[href],button,input,select,textarea,[role="button"],[onclick]'))
        .filter(visible);
      const controls = visibleControls
        .slice(0, maxFeatures)
        .map((el, index) => ({
          text: label(el),
          candidate_index: index,
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


def _side_effect_click_expression() -> str:
    return """async (candidate) => {
      const visible = (el) => {
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none';
      };
      const label = (el) => (
        el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') ||
        el.getAttribute('placeholder') || el.value || el.name || el.id || el.tagName
      ).trim().replace(/\\s+/g, ' ').slice(0, 80);
      window.__stage2DialogLog = Array.isArray(window.__stage2DialogLog) ? window.__stage2DialogLog : [];
      if (!window.__stage2DialogPatched) {
        const originalAlert = window.alert.bind(window);
        const originalConfirm = window.confirm.bind(window);
        const originalPrompt = window.prompt.bind(window);
        window.alert = (message) => {
          window.__stage2DialogLog.push({type: 'alert', message: String(message), handled: 'recorded'});
          return undefined;
        };
        window.confirm = (message) => {
          window.__stage2DialogLog.push({type: 'confirm', message: String(message), handled: 'accepted'});
          return true;
        };
        window.prompt = (message, defaultValue = '') => {
          window.__stage2DialogLog.push({type: 'prompt', message: String(message), handled: 'default_value'});
          return String(defaultValue || '');
        };
        window.__stage2OriginalDialogFns = {originalAlert, originalConfirm, originalPrompt};
        window.__stage2DialogPatched = true;
      }
      const controls = Array.from(document.querySelectorAll('a[href],button,input,select,textarea,[role="button"],[onclick]'))
        .filter(visible);
      const targetText = String(candidate.control_label || candidate.control?.text || '').trim();
      const index = Number(candidate.candidate_index || 0);
      const exactMatches = controls.filter((el) => label(el) === targetText);
      const indexed = controls[index];
      const target = exactMatches[0] || (indexed && label(indexed) === targetText ? indexed : null);
      if (!target) {
        return {
          clicked: false,
          reason: 'control_not_found',
          url_before: location.href,
          url_after: location.href,
          visible_labels: controls.slice(0, 12).map(label),
          dialog_events: window.__stage2DialogLog.slice(-10)
        };
      }
      if (target.disabled || target.getAttribute('aria-disabled') === 'true') {
        return {
          clicked: false,
          reason: 'control_disabled',
          url_before: location.href,
          url_after: location.href,
          matched_label: label(target),
          dialog_events: window.__stage2DialogLog.slice(-10)
        };
      }
      const urlBefore = location.href;
      target.scrollIntoView({block: 'center', inline: 'center'});
      await new Promise((resolve) => window.setTimeout(resolve, 120));
      target.click();
      await new Promise((resolve) => window.setTimeout(resolve, 120));
      return {
        clicked: true,
        reason: null,
        url_before: urlBefore,
        url_after: location.href,
        matched_label: label(target),
        dialog_events: window.__stage2DialogLog.slice(-10)
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
                "requires_test_data": feature_type in SIDE_EFFECT_ACTION_TYPES,
                "evidence": {
                    "screenshot_id": screenshot_id,
                    "tag": control.get("tag"),
                    "type": control.get("type"),
                    "candidate_index": control.get("candidate_index"),
                },
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


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _text(value: Any) -> str:
    return str(value or "").strip()


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
    if any(word in lowered for word in ("approve", "审批", "审核")):
        return "approve"
    if any(word in lowered for word in ("save", "保存")):
        return "save"
    if any(word in lowered for word in ("submit", "提交")):
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
    if feature_type in SIDE_EFFECT_ACTION_TYPES:
        return "high"
    return "low"
