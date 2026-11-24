"""Запись SyncLog для операций синхронизации с Keitaro."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session

from app.models import SyncLog


def write_sync_log(
    db: Session,
    *,
    campaign_id: int,
    action: str,
    status: str,
    summary: str,
    stream_id: int | None = None,
    commit: bool = False,
) -> SyncLog:
    row = SyncLog(
        campaign_id=campaign_id,
        stream_id=stream_id,
        action=action,
        status=status,
        summary=(summary or "")[:4000],
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def _short_name(name: str, max_len: int = 32) -> str:
    text = (name or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def stream_label(stream: Any) -> str:
    name = getattr(stream, "name", None) or f"#{getattr(stream, 'keitaro_stream_id', '?')}"
    return f"«{_short_name(str(name), 40)}»"


def format_push_summary(stream: Any, offers: Iterable[Any]) -> str:
    """Одна строка: поток и список офферов с долями (без дубля «PUSH:»)."""
    items = list(offers)
    label = stream_label(stream)
    if not items:
        return f"{label}: без офферов"

    parts: list[str] = []
    limit = 6
    for offer in items[:limit]:
        oid = getattr(offer, "keitaro_offer_id", "?")
        name = _short_name(str(getattr(offer, "offer_name", "") or f"Offer {oid}"))
        share = getattr(offer, "share", 0)
        pin = " pin" if getattr(offer, "pinned", False) else ""
        parts.append(f"{oid} {name} {share}%{pin}")
    body = " · ".join(parts)
    extra = len(items) - limit
    if extra > 0:
        body += f" · ещё {extra}"
    return f"{label}: {body}"


def format_cancel_summary(stream: Any, *, restored: bool, diff: dict[str, Any] | None) -> str:
    label = stream_label(stream)
    if not restored:
        return f"{label}: snapshot пуст, draft снят"
    bits: list[str] = []
    if diff and not diff.get("empty"):
        if diff.get("added"):
            bits.append(f"+{len(diff['added'])}")
        if diff.get("removed"):
            bits.append(f"−{len(diff['removed'])}")
        if diff.get("share_changed"):
            bits.append(f"доли {len(diff['share_changed'])}")
        if diff.get("pin_changed"):
            bits.append(f"pin {len(diff['pin_changed'])}")
    if bits:
        return f"{label}: откат ({', '.join(bits)})"
    return f"{label}: откат к опубликованному"


def format_fetch_summary(*, streams_from_kt: int, removed_local: int, force: bool) -> str:
    parts = [f"из KT: {streams_from_kt} поток(ов)"]
    if removed_local:
        parts.append(f"удалено локальных: {removed_local}")
    text = " · ".join(parts)
    if force:
        text += " · черновик перезаписан"
    return text
