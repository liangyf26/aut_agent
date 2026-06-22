from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

_VOLATILE_QUERY_KEYS = {
    "_",
    "_t",
    "cachebust",
    "nonce",
    "rand",
    "random",
    "timestamp",
    "token",
    "ts",
}
_ENTITY_QUERY_KEYS = {
    "bizid",
    "dataid",
    "detailid",
    "entityid",
    "formid",
    "id",
    "itemid",
    "objectid",
    "pk",
    "recordid",
    "rowid",
    "taskid",
    "uuid",
}
_ENTITY_PATH_HINTS = {
    "detail",
    "details",
    "edit",
    "info",
    "item",
    "items",
    "record",
    "records",
    "row",
    "task",
    "view",
}
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_LONG_TOKEN_RE = re.compile(r"^[0-9a-z]{10,}$", re.IGNORECASE)
_NUMERIC_ID_RE = re.compile(r"^\d{5,}$")
_MIXED_ID_RE = re.compile(r"^(?:[a-z]{0,4}\d{5,}|\d{5,}[a-z]{0,4})$", re.IGNORECASE)


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(str(value).split()).strip()


def slug(value: str | None, *, fallback: str = "item", max_length: int = 48) -> str:
    collapsed = []
    last_was_separator = False
    for ch in normalize_text(value).lower():
        if ch.isalnum():
            collapsed.append(ch)
            last_was_separator = False
            continue
        if not last_was_separator:
            collapsed.append("_")
        last_was_separator = True
    result = "".join(collapsed).strip("_")
    if max_length and len(result) > max_length:
        result = result[:max_length].strip("_")
    return result or fallback


def absolutize_url(url: str | None, *, base_url: str | None = None) -> str:
    raw = normalize_text(url)
    if not raw:
        return ""
    if base_url:
        return normalize_text(urljoin(base_url, raw))
    return raw


def canonicalize_url(url: str | None, *, base_url: str | None = None) -> str:
    raw = absolutize_url(url, base_url=base_url)
    if not raw:
        return ""
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query_items = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key and key.lower() not in _VOLATILE_QUERY_KEYS
    ]
    query_items.sort(key=lambda item: (item[0], item[1]))
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(query_items, doseq=True),
            "",
        )
    )


def generalize_url_for_identity(url: str | None, *, base_url: str | None = None) -> str:
    canonical = canonicalize_url(url, base_url=base_url)
    if not canonical:
        return ""
    parsed = urlsplit(canonical)
    path_segments = [segment for segment in parsed.path.split("/") if segment]
    if path_segments:
        rewritten_segments: list[str] = []
        for index, segment in enumerate(path_segments):
            previous = path_segments[index - 1].lower() if index > 0 else ""
            if _should_generalize_path_segment(segment, previous=previous):
                rewritten_segments.append(":id")
            else:
                rewritten_segments.append(segment)
        path = "/" + "/".join(rewritten_segments)
    else:
        path = parsed.path or "/"

    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        normalized_key = key.lower()
        if normalized_key in _ENTITY_QUERY_KEYS or _looks_like_entity_id(value):
            query_items.append((key, ":id"))
        else:
            query_items.append((key, value))
    query_items.sort(key=lambda item: (item[0], item[1]))
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            urlencode(query_items, doseq=True),
            "",
        )
    )


def locator_signature(locator: str | None) -> str:
    value = normalize_text(locator)
    if not value:
        return ""
    compact = re.sub(r":nth-of-type\(\d+\)", "", value)
    compact = re.sub(r"\s*>\s*", " > ", compact)
    compact = re.sub(r"\s+", " ", compact)
    return compact.strip()


def locator_anchor(locator: str | None, *, keep_segments: int = 3) -> str:
    signature = locator_signature(locator)
    if not signature:
        return ""
    segments = [segment.strip() for segment in signature.split(">") if segment.strip()]
    if len(segments) > keep_segments:
        segments = segments[-keep_segments:]
    return " > ".join(segments)


def build_page_entry_identity(
    template_name: str,
    *,
    name: str,
    url: str,
) -> dict[str, Any]:
    normalized_name = normalize_text(name)
    canonical_url = canonicalize_url(url)
    generalized_url = generalize_url_for_identity(url)
    stable_target = generalized_url or canonical_url or normalized_name.lower() or "entry"
    stable_key = f"page_entry|{stable_target}"
    dedupe_basis = {
        "canonical_url": canonical_url or None,
        "generalized_url": generalized_url or None,
        "normalized_name": normalized_name or None,
    }
    readable = _page_entry_readable_name(normalized_name, generalized_url or canonical_url)
    return {
        "record_id": build_record_id(
            template_name=template_name,
            record_type="page_entry",
            readable_name=readable,
            stable_key=stable_key,
        ),
        "stable_key": stable_key,
        "dedupe_key": stable_key,
        "dedupe_basis": dedupe_basis,
    }


def build_feature_point_identity(
    template_name: str,
    *,
    page_entry_key: str,
    name: str,
    feature_scope: str,
    action_type: str,
    container_label: str | None = None,
    href: str | None = None,
    page_url: str | None = None,
    locator: str | None = None,
) -> dict[str, Any]:
    normalized_name = normalize_text(name)
    normalized_container = normalize_text(container_label)
    canonical_href = canonicalize_url(href, base_url=page_url)
    generalized_href = generalize_url_for_identity(href, base_url=page_url)
    locator_hint = locator_anchor(locator)
    stable_parts = [
        "feature_point",
        page_entry_key,
        feature_scope or "page_action",
        action_type or "trigger",
        normalized_name.lower() or "feature",
    ]
    if generalized_href:
        stable_parts.append(generalized_href)
    elif canonical_href:
        stable_parts.append(canonical_href)
    elif normalized_container:
        stable_parts.append(normalized_container.lower())
    elif locator_hint:
        stable_parts.append(locator_hint.lower())
    stable_key = "|".join(stable_parts)
    dedupe_basis = {
        "page_entry_key": page_entry_key,
        "feature_scope": feature_scope or "page_action",
        "action_type": action_type or "trigger",
        "normalized_name": normalized_name or None,
        "canonical_href": canonical_href or None,
        "generalized_href": generalized_href or None,
        "container_label": normalized_container or None,
        "locator_anchor": locator_hint or None,
    }
    readable_parts = [normalized_name or "feature", feature_scope or "page", action_type or "trigger"]
    return {
        "record_id": build_record_id(
            template_name=template_name,
            record_type="feature_point",
            readable_name="_".join(readable_parts),
            stable_key=stable_key,
        ),
        "stable_key": stable_key,
        "dedupe_key": stable_key,
        "dedupe_basis": dedupe_basis,
    }


def build_record_id(
    *,
    template_name: str,
    record_type: str,
    readable_name: str,
    stable_key: str,
) -> str:
    return (
        f"{template_name}__{record_type}__"
        f"{slug(readable_name, fallback=record_type)}__{_short_hash(record_type, stable_key)}"
    )


def _page_entry_readable_name(normalized_name: str, canonical_url: str) -> str:
    parsed = urlsplit(canonical_url)
    path_parts = [part for part in parsed.path.split("/") if part]
    if path_parts:
        readable = "_".join(path_parts[-2:])
        if parsed.query:
            query_hint = slug(parsed.query, fallback="")
            if query_hint:
                return f"{readable}_{query_hint}"
        return readable
    return normalized_name or "entry"


def _looks_like_entity_id(value: str | None) -> bool:
    normalized = normalize_text(value)
    if not normalized:
        return False
    if _UUID_RE.fullmatch(normalized):
        return True
    if _NUMERIC_ID_RE.fullmatch(normalized):
        return True
    if _MIXED_ID_RE.fullmatch(normalized):
        return True
    if _LONG_TOKEN_RE.fullmatch(normalized) and any(ch.isdigit() for ch in normalized):
        return True
    return False


def _should_generalize_path_segment(segment: str, *, previous: str) -> bool:
    normalized = normalize_text(segment)
    if not normalized:
        return False
    if previous in _ENTITY_PATH_HINTS and _looks_like_entity_id(normalized):
        return True
    if _UUID_RE.fullmatch(normalized):
        return True
    if len(normalized) >= 12 and _LONG_TOKEN_RE.fullmatch(normalized) and any(ch.isdigit() for ch in normalized):
        return True
    if _NUMERIC_ID_RE.fullmatch(normalized):
        return True
    return False


def _short_hash(*parts: str) -> str:
    payload = "|".join(part for part in parts if part)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]
