#!/usr/bin/env python
"""Probe OpenAI-compatible LLM capabilities for the stage-2 prototype.

The probe intentionally separates raw API capabilities from Browser Use wrapper
capabilities. A model can return JSON and still fail Browser Use's structured
output path if the wrapper sends a protocol shape the provider does not accept.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from openai import OpenAI
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = PROJECT_ROOT / "demo" / ".env"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "model_capability_probe"


class ProbeAnswer(BaseModel):
    ok: bool = Field(description="Whether the probe instruction was followed.")
    value: str = Field(description="Short value requested by the probe.")


class ProbeStepResult(BaseModel):
    name: str
    ok: bool
    elapsed_ms: int
    detail: dict[str, Any]
    error: str | None = None


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")

    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        values[key] = value
    return values


def get_config(env_path: Path) -> dict[str, str]:
    values = load_env_file(env_path)
    config = {
        "base_url": values.get("LOCAL_LLM_BASE_URL") or os.getenv("LOCAL_LLM_BASE_URL") or "",
        "api_key": values.get("LOCAL_LLM_API_KEY") or os.getenv("LOCAL_LLM_API_KEY") or "",
        "model": values.get("LOCAL_LLM_MODEL") or os.getenv("LOCAL_LLM_MODEL") or "",
    }
    missing = [key for key, value in config.items() if not value]
    if missing:
        raise ValueError(f"Missing required LLM config values: {', '.join(missing)}")
    return config


def elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def summarize_error(exc: BaseException) -> str:
    text = str(exc)
    if len(text) > 1000:
        return text[:1000] + "...<truncated>"
    return text


def run_step(name: str, func: Callable[[], dict[str, Any]]) -> ProbeStepResult:
    start = time.perf_counter()
    try:
        detail = func()
        return ProbeStepResult(name=name, ok=True, elapsed_ms=elapsed_ms(start), detail=detail)
    except Exception as exc:  # noqa: BLE001 - probe must capture provider-specific failures.
        return ProbeStepResult(
            name=name,
            ok=False,
            elapsed_ms=elapsed_ms(start),
            detail={},
            error=summarize_error(exc),
        )


async def run_async_step(name: str, func: Callable[[], Any]) -> ProbeStepResult:
    start = time.perf_counter()
    try:
        detail = await func()
        return ProbeStepResult(name=name, ok=True, elapsed_ms=elapsed_ms(start), detail=detail)
    except Exception as exc:  # noqa: BLE001 - probe must capture provider-specific failures.
        return ProbeStepResult(
            name=name,
            ok=False,
            elapsed_ms=elapsed_ms(start),
            detail={},
            error=summarize_error(exc),
        )


def first_content(response: Any) -> str:
    return response.choices[0].message.content or ""


def probe_basic_chat(client: OpenAI, model: str) -> dict[str, Any]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Reply with exactly one word."},
            {"role": "user", "content": "Reply with: pong"},
        ],
        temperature=0,
    )
    content = first_content(response)
    return {
        "content_preview": content[:200],
        "finish_reason": response.choices[0].finish_reason,
        "matched": content.strip().lower() == "pong",
    }


def probe_json_object(client: OpenAI, model: str) -> dict[str, Any]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Return only JSON."},
            {"role": "user", "content": 'Return {"ok": true, "value": "json_object"}.'},
        ],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = first_content(response)
    parsed = json.loads(content)
    return {
        "content_preview": content[:200],
        "parsed": parsed,
        "matched": parsed.get("ok") is True and parsed.get("value") == "json_object",
    }


def probe_json_schema(client: OpenAI, model: str) -> dict[str, Any]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Return data that satisfies the provided schema."},
            {"role": "user", "content": 'Return ok=true and value="json_schema".'},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "capability_probe",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "ok": {"type": "boolean"},
                        "value": {"type": "string"},
                    },
                    "required": ["ok", "value"],
                },
            },
        },
        temperature=0,
    )
    content = first_content(response)
    parsed = ProbeAnswer.model_validate_json(content)
    return {
        "content_preview": content[:200],
        "parsed": parsed.model_dump(),
        "matched": parsed.ok is True and parsed.value == "json_schema",
    }


def probe_tool_call(client: OpenAI, model: str) -> dict[str, Any]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Use the provided tool to return the answer."},
            {"role": "user", "content": 'Call record_probe with ok=true and value="tool_call".'},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "record_probe",
                    "description": "Record the probe answer.",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "ok": {"type": "boolean"},
                            "value": {"type": "string"},
                        },
                        "required": ["ok", "value"],
                    },
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": "record_probe"}},
        temperature=0,
    )
    message = response.choices[0].message
    if not message.tool_calls:
        raise ValueError("Expected tool_calls in response but got none.")
    args = json.loads(message.tool_calls[0].function.arguments)
    return {
        "tool_name": message.tool_calls[0].function.name,
        "arguments": args,
        "matched": args.get("ok") is True and args.get("value") == "tool_call",
    }


def probe_tool_call_auto(client: OpenAI, model: str) -> dict[str, Any]:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Use the provided tool to return the answer."},
            {"role": "user", "content": 'Call record_probe with ok=true and value="tool_call_auto".'},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "record_probe",
                    "description": "Record the probe answer.",
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "ok": {"type": "boolean"},
                            "value": {"type": "string"},
                        },
                        "required": ["ok", "value"],
                    },
                },
            }
        ],
        temperature=0,
    )
    message = response.choices[0].message
    if not message.tool_calls:
        raise ValueError(f"Expected tool_calls in response but got none. content={message.content!r}")
    args = json.loads(message.tool_calls[0].function.arguments)
    return {
        "tool_name": message.tool_calls[0].function.name,
        "arguments": args,
        "matched": args.get("ok") is True and args.get("value") == "tool_call_auto",
    }


def chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def probe_raw_http_json_schema(config: dict[str, str], timeout: float) -> dict[str, Any]:
    payload = {
        "model": config["model"],
        "messages": [
            {"role": "system", "content": "Return data that satisfies the provided schema."},
            {"role": "user", "content": 'Return ok=true and value="raw_http_json_schema".'},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "raw_capability_probe",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "ok": {"type": "boolean"},
                        "value": {"type": "string"},
                    },
                    "required": ["ok", "value"],
                },
            },
        },
        "temperature": 0,
    }
    headers = {
        "authorization": f"Bearer {config['api_key']}",
        "content-type": "application/json",
    }
    response = httpx.post(chat_completions_url(config["base_url"]), headers=headers, json=payload, timeout=timeout)
    if response.status_code >= 400:
        raise ValueError(f"HTTP {response.status_code}: {response.text[:1000]}")
    body = response.json()
    content = body["choices"][0]["message"].get("content") or ""
    parsed = ProbeAnswer.model_validate_json(content)
    return {
        "status_code": response.status_code,
        "content_preview": content[:200],
        "parsed": parsed.model_dump(),
        "matched": parsed.ok is True and parsed.value == "raw_http_json_schema",
    }


async def probe_browser_use_chatopenai(config: dict[str, str]) -> dict[str, Any]:
    from browser_use import ChatOpenAI
    from browser_use.llm.messages import SystemMessage, UserMessage

    llm = ChatOpenAI(
        model=config["model"],
        api_key=config["api_key"],
        base_url=config["base_url"],
        timeout=60,
        max_retries=0,
        temperature=0,
    )
    result = await llm.ainvoke(
        [
            SystemMessage(content="Return data that satisfies the requested output format."),
            UserMessage(content='Return ok=true and value="browser_use_chatopenai".'),
        ],
        output_format=ProbeAnswer,
    )
    parsed = result.completion
    return {
        "parsed": parsed.model_dump(),
        "matched": parsed.ok is True and parsed.value == "browser_use_chatopenai",
        "provider": llm.provider,
    }


async def probe_browser_use_chatdeepseek(config: dict[str, str]) -> dict[str, Any]:
    from browser_use.llm.deepseek.chat import ChatDeepSeek
    from browser_use.llm.messages import SystemMessage, UserMessage

    llm = ChatDeepSeek(
        model=config["model"],
        api_key=config["api_key"],
        base_url=config["base_url"],
        timeout=60,
        temperature=0,
    )
    result = await llm.ainvoke(
        [
            SystemMessage(content="Return data that satisfies the requested output format."),
            UserMessage(content='Return ok=true and value="browser_use_chatdeepseek".'),
        ],
        output_format=ProbeAnswer,
    )
    parsed = result.completion
    return {
        "parsed": parsed.model_dump(),
        "matched": parsed.ok is True and parsed.value == "browser_use_chatdeepseek",
        "provider": llm.provider,
    }


def capability_tags(results: list[ProbeStepResult]) -> dict[str, bool]:
    by_name = {item.name: item for item in results}
    return {
        "chat_completion": by_name.get("basic_chat", ProbeStepResult(name="", ok=False, elapsed_ms=0, detail={})).ok,
        "json_object_response_format": by_name.get("json_object", ProbeStepResult(name="", ok=False, elapsed_ms=0, detail={})).ok,
        "json_schema_response_format": by_name.get("json_schema", ProbeStepResult(name="", ok=False, elapsed_ms=0, detail={})).ok,
        "tool_calling": by_name.get("tool_call", ProbeStepResult(name="", ok=False, elapsed_ms=0, detail={})).ok,
        "tool_calling_auto": by_name.get(
            "tool_call_auto",
            ProbeStepResult(name="", ok=False, elapsed_ms=0, detail={}),
        ).ok,
        "raw_http_json_schema": by_name.get(
            "raw_http_json_schema",
            ProbeStepResult(name="", ok=False, elapsed_ms=0, detail={}),
        ).ok,
        "browser_use_chatopenai_structured": by_name.get(
            "browser_use_chatopenai_structured",
            ProbeStepResult(name="", ok=False, elapsed_ms=0, detail={}),
        ).ok,
        "browser_use_chatdeepseek_structured": by_name.get(
            "browser_use_chatdeepseek_structured",
            ProbeStepResult(name="", ok=False, elapsed_ms=0, detail={}),
        ).ok,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Probe LLM capabilities for aut_agent stage-2 routing.")
    parser.add_argument("--env", default=str(DEFAULT_ENV), help="Env file containing LOCAL_LLM_* values.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for JSON probe report.")
    parser.add_argument("--timeout", type=float, default=60, help="OpenAI client request timeout in seconds.")
    parser.add_argument("--skip-browser-use", action="store_true", help="Skip Browser Use wrapper probes.")
    parser.add_argument(
        "--unicode-output",
        action="store_true",
        help="Print Unicode JSON to stdout. By default stdout is ASCII-safe for Windows consoles.",
    )
    args = parser.parse_args()

    env_path = Path(args.env).resolve()
    output_dir = Path(args.output_dir).resolve()
    config = get_config(env_path)
    client = OpenAI(api_key=config["api_key"], base_url=config["base_url"], timeout=args.timeout, max_retries=0)

    results: list[ProbeStepResult] = [
        run_step("basic_chat", lambda: probe_basic_chat(client, config["model"])),
        run_step("json_object", lambda: probe_json_object(client, config["model"])),
        run_step("json_schema", lambda: probe_json_schema(client, config["model"])),
        run_step("tool_call", lambda: probe_tool_call(client, config["model"])),
        run_step("tool_call_auto", lambda: probe_tool_call_auto(client, config["model"])),
        run_step("raw_http_json_schema", lambda: probe_raw_http_json_schema(config, args.timeout)),
    ]

    if not args.skip_browser_use:
        results.append(
            await run_async_step("browser_use_chatopenai_structured", lambda: probe_browser_use_chatopenai(config))
        )
        results.append(
            await run_async_step("browser_use_chatdeepseek_structured", lambda: probe_browser_use_chatdeepseek(config))
        )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "env_file": str(env_path),
        "base_url": config["base_url"],
        "model": config["model"],
        "capability_tags": capability_tags(results),
        "results": [item.model_dump() for item in results],
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_model = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in config["model"])
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"{timestamp}_{safe_model}.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(report, ensure_ascii=args.unicode_output is False, indent=2))
    print(f"\nReport written: {output_path}")
    client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001 - CLI should return readable failure.
        print(f"Probe failed: {summarize_error(exc)}", file=sys.stderr)
        raise SystemExit(1)
