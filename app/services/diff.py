"""Diff черновика относительно последнего опубликованного snapshot."""

from __future__ import annotations

from typing import Any


def build_stream_diff(stream: Any) -> dict[str, Any] | None:
    """
    Сравнивает текущие офферы потока с published_snapshot.
    Возвращает None, если не draft или нет изменений.
    """
    if not getattr(stream, "is_draft", False):
        return None

    snap_offers = []
    snap = getattr(stream, "published_snapshot", None) or {}
    if isinstance(snap, dict):
        snap_offers = snap.get("offers") or []

    before_by_id: dict[int, dict] = {
        int(o["keitaro_offer_id"]): o for o in snap_offers
    }
    after_by_id: dict[int, Any] = {
        o.keitaro_offer_id: o for o in (stream.offers or [])
    }

    added: list[dict] = []
    removed: list[dict] = []
    share_changed: list[dict] = []
    pin_changed: list[dict] = []

    all_ids = set(before_by_id) | set(after_by_id)

    for oid in sorted(all_ids):
        before = before_by_id.get(oid)
        after = after_by_id.get(oid)

        before_status = (before or {}).get("status", "removed")
        after_status = after.status if after else "removed"
        before_active = before_status == "active"
        after_active = after_status == "active"

        name = (
            (after.offer_name if after else None)
            or (before or {}).get("offer_name")
            or f"Offer {oid}"
        )

        if after_active and not before_active:
            added.append(
                {
                    "keitaro_offer_id": oid,
                    "offer_name": name,
                    "share": after.share if after else 0,
                    "pinned": bool(after.pinned) if after else False,
                }
            )
            continue

        if before_active and not after_active:
            removed.append(
                {
                    "keitaro_offer_id": oid,
                    "offer_name": name,
                    "share_before": int((before or {}).get("share") or 0),
                }
            )
            continue

        if not after_active and not before_active:
            continue

        # оба active
        b_share = int((before or {}).get("share") or 0)
        a_share = after.share if after else 0
        b_pin = bool((before or {}).get("pinned"))
        a_pin = bool(after.pinned) if after else False

        if b_share != a_share:
            share_changed.append(
                {
                    "keitaro_offer_id": oid,
                    "offer_name": name,
                    "share_before": b_share,
                    "share_after": a_share,
                }
            )
        if b_pin != a_pin:
            pin_changed.append(
                {
                    "keitaro_offer_id": oid,
                    "offer_name": name,
                    "pinned_before": b_pin,
                    "pinned_after": a_pin,
                }
            )

    if not (added or removed or share_changed or pin_changed):
        return {
            "added": [],
            "removed": [],
            "share_changed": [],
            "pin_changed": [],
            "empty": True,
        }

    return {
        "added": added,
        "removed": removed,
        "share_changed": share_changed,
        "pin_changed": pin_changed,
        "empty": False,
    }
