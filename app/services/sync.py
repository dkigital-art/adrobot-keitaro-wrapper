"""FETCH / PUSH / CANCEL и локальные операции над офферами потока."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, joinedload

from app.models import Campaign, Stream, StreamOffer, SyncLog
from app.services.diff import build_stream_diff
from app.services.keitaro import KeitaroClient, KeitaroError
from app.services.logging_sync import (
    format_cancel_summary,
    format_fetch_summary,
    format_push_summary,
    write_sync_log,
)
from app.services.weights import (
    MIN_ACTIVE_SHARE,
    OfferWeight,
    WeightError,
    redistribute,
    set_pinned_share,
    set_unpinned_share,
    validate_pinned_state,
)


class DraftConflictError(Exception):
    """В кампании есть draft-потоки; FETCH без force запрещён."""

    def __init__(self, message: str, draft_stream_ids: list[int]):
        super().__init__(message)
        self.draft_stream_ids = draft_stream_ids


class CampaignMissingInKeitaroError(Exception):
    """Кампания удалена в Keitaro — локальная копия осталась без родителя."""

    def __init__(self, *, campaign_id: int, keitaro_campaign_id: int):
        super().__init__(
            f"Кампания KT #{keitaro_campaign_id} удалена в Keitaro. "
            "Восстановить её PUSH-ем нельзя — удалите локальную копию."
        )
        self.campaign_id = campaign_id
        self.keitaro_campaign_id = keitaro_campaign_id


def _snapshot_offers(stream: Stream) -> list[dict[str, Any]]:
    return [
        {
            "keitaro_offer_id": o.keitaro_offer_id,
            "offer_name": o.offer_name,
            "share": o.share,
            "pinned": o.pinned,
            "status": o.status,
            "sort_order": o.sort_order,
        }
        for o in sorted(stream.offers, key=lambda x: x.sort_order)
    ]


def save_published_snapshot(stream: Stream) -> None:
    stream.published_snapshot = {"offers": _snapshot_offers(stream)}
    stream.is_draft = False


def mark_draft(stream: Stream) -> None:
    stream.is_draft = True


def _to_weights(stream: Stream) -> list[OfferWeight]:
    return [
        OfferWeight(
            id=o.id,
            share=o.share,
            pinned=o.pinned,
            status=o.status,
        )
        for o in stream.offers
    ]


def _from_weights(stream: Stream, items: list[OfferWeight]) -> None:
    by_id = {i.id: i for i in items}
    for o in stream.offers:
        w = by_id[o.id]
        o.share = w.share
        o.status = w.status
        o.pinned = w.pinned


def apply_weights(stream: Stream) -> None:
    items = _to_weights(stream)
    redistribute(items)
    _from_weights(stream, items)


def _log_error(
    db: Session,
    *,
    campaign_id: int,
    action: str,
    exc: Exception,
    stream_id: int | None = None,
) -> None:
    """Откатывает незавершённые изменения сессии и отдельно фиксирует ошибку в журнале."""
    db.rollback()
    write_sync_log(
        db,
        campaign_id=campaign_id,
        stream_id=stream_id,
        action=action,
        status="error",
        summary=str(exc),
        commit=True,
    )


def get_stream_or_404(db: Session, stream_id: int) -> Stream:
    stream = (
        db.query(Stream)
        .options(joinedload(Stream.offers), joinedload(Stream.campaign))
        .filter(Stream.id == stream_id)
        .first()
    )
    if not stream:
        raise LookupError(f"Поток #{stream_id} не найден")
    return stream


def get_campaign_or_404(db: Session, campaign_id: int) -> Campaign:
    campaign = (
        db.query(Campaign)
        .options(joinedload(Campaign.streams).joinedload(Stream.offers))
        .filter(Campaign.id == campaign_id)
        .first()
    )
    if not campaign:
        raise LookupError(f"Кампания #{campaign_id} не найдена")
    return campaign


def fetch_streams_from_keitaro(
    db: Session,
    campaign: Campaign,
    client: KeitaroClient,
    *,
    force: bool = False,
) -> Campaign:
    """Синхронизация потоков кампании из Keitaro в локальную БД."""
    campaign_id = campaign.id
    draft_streams = [s for s in campaign.streams if s.is_draft]
    if draft_streams and not force:
        ids = [s.id for s in draft_streams]
        names = ", ".join(s.name for s in draft_streams)
        raise DraftConflictError(
            f"В потоках есть неопубликованные изменения ({names}). "
            "Передайте force=true, чтобы перезаписать данными из Keitaro.",
            draft_stream_ids=ids,
        )

    try:
        try:
            kt_streams = client.list_campaign_streams(campaign.keitaro_campaign_id)
        except KeitaroError as exc:
            if exc.status_code == 404:
                raise CampaignMissingInKeitaroError(
                    campaign_id=campaign_id,
                    keitaro_campaign_id=campaign.keitaro_campaign_id,
                ) from exc
            raise
        detailed: list[dict] = []
        for row in kt_streams:
            sid = row.get("id")
            if sid is None:
                continue
            try:
                detailed.append(client.get_stream(int(sid)))
            except KeitaroError:
                # без состава офферов нельзя отличить «удалён в KT» от «не загрузился»
                if "offers" not in row:
                    raise
                detailed.append(row)

        # force: сначала выбрасываем локальный черновик, чтобы не плодить «призраков»
        for stream in draft_streams:
            _restore_from_snapshot(stream)
        if draft_streams:
            db.flush()

        existing_by_kt = {s.keitaro_stream_id: s for s in campaign.streams}
        kt_ids = {int(kt["id"]) for kt in detailed}
        stale = [s for s in campaign.streams if s.keitaro_stream_id not in kt_ids]

        if not campaign.geo:
            geo = _extract_geo_from_streams(detailed)
            if geo:
                campaign.geo = geo

        for kt in detailed:
            kt_id = int(kt["id"])
            offers_raw = kt.get("offers") or []
            has_offers = bool(offers_raw) or str(kt.get("schema", "")).lower() in {
                "landings",
                "offers",
            }

            stream = existing_by_kt.get(kt_id)
            if stream is None:
                stream = Stream(
                    campaign_id=campaign.id,
                    keitaro_stream_id=kt_id,
                    name=kt.get("name") or f"Stream {kt_id}",
                    type=kt.get("type") or "regular",
                    stream_schema=kt.get("schema") or "redirect",
                    has_offers=has_offers,
                    is_draft=False,
                )
                db.add(stream)
                db.flush()
                campaign.streams.append(stream)
            else:
                stream.name = kt.get("name") or stream.name
                stream.type = kt.get("type") or stream.type
                stream.stream_schema = kt.get("schema") or stream.stream_schema
                stream.has_offers = has_offers or stream.has_offers

            if not has_offers and not offers_raw:
                save_published_snapshot(stream)
                continue

            stream.has_offers = True
            _merge_offers_from_keitaro(stream, offers_raw, client)
            save_published_snapshot(stream)

        if stale:
            stale_ids = [s.id for s in stale]
            db.query(SyncLog).filter(SyncLog.stream_id.in_(stale_ids)).update(
                {SyncLog.stream_id: None}, synchronize_session=False
            )
            for stream in stale:
                campaign.streams.remove(stream)

        write_sync_log(
            db,
            campaign_id=campaign_id,
            action="fetch",
            status="ok",
            summary=format_fetch_summary(
                streams_from_kt=len(detailed),
                removed_local=len(stale),
                force=force,
            ),
        )
        db.commit()
    except (KeitaroError, CampaignMissingInKeitaroError) as exc:
        _log_error(db, campaign_id=campaign_id, action="fetch", exc=exc)
        raise

    db.refresh(campaign)
    return get_campaign_or_404(db, campaign_id)


def _merge_offers_from_keitaro(
    stream: Stream,
    offers_raw: list[dict],
    client: KeitaroClient,
) -> None:
    """
    Обновляет локальные офферы по данным Keitaro.
    Оффер, пропавший из KT, не удаляется — status=removed.
    """
    local_by_offer = {o.keitaro_offer_id: o for o in stream.offers}
    seen: set[int] = set()

    for idx, item in enumerate(offers_raw):
        offer_id = int(item.get("offer_id") or item.get("id"))
        seen.add(offer_id)
        share = int(item.get("share") or 0)
        state = str(item.get("state") or "active").lower()
        status = "active" if state == "active" and share > 0 else "removed"

        name = item.get("name") or item.get("offer_name") or ""
        if not name:
            try:
                offer = client.get_offer(offer_id)
                name = offer.get("name") or f"Offer {offer_id}"
            except KeitaroError:
                name = f"Offer {offer_id}"

        local = local_by_offer.get(offer_id)
        if local is None:
            stream.offers.append(
                StreamOffer(
                    stream_id=stream.id,
                    keitaro_offer_id=offer_id,
                    offer_name=name,
                    share=share if status == "active" else 0,
                    pinned=False,
                    status=status,
                    sort_order=idx,
                )
            )
        else:
            local.offer_name = name or local.offer_name
            local.share = share if status == "active" else 0
            local.status = status
            local.sort_order = idx
            # pin сохраняем локально

    # офферы, которых больше нет в KT → removed (не стираем)
    for local in stream.offers:
        if local.keitaro_offer_id not in seen and local.status == "active":
            local.status = "removed"
            local.share = 0


def add_offer_to_stream(
    db: Session,
    stream: Stream,
    offer_id: int,
    client: KeitaroClient,
) -> Stream:
    if not stream.has_offers:
        raise ValueError("В этот поток нельзя добавлять офферы (нет схемы landings)")

    existing = next((o for o in stream.offers if o.keitaro_offer_id == offer_id), None)
    if existing and existing.status == "active":
        raise ValueError(f"Оффер {offer_id} уже есть в потоке")

    try:
        kt_offer = client.get_offer(offer_id)
        name = kt_offer.get("name") or f"Offer {offer_id}"
    except KeitaroError as exc:
        if exc.status_code == 404:
            raise ValueError(f"Оффер {offer_id} не найден в Keitaro") from exc
        raise

    if existing:
        existing.status = "active"
        existing.offer_name = name
        existing.pinned = False
    else:
        next_order = max((o.sort_order for o in stream.offers), default=-1) + 1
        stream.offers.append(
            StreamOffer(
                keitaro_offer_id=offer_id,
                offer_name=name,
                share=0,
                pinned=False,
                status="active",
                sort_order=next_order,
            )
        )

    apply_weights(stream)
    mark_draft(stream)
    db.commit()
    return get_stream_or_404(db, stream.id)


def remove_offer(db: Session, stream: Stream, offer_row_id: int) -> Stream:
    offer = next((o for o in stream.offers if o.id == offer_row_id), None)
    if not offer:
        raise LookupError("Оффер не найден в потоке")
    offer.status = "removed"
    offer.share = 0
    # pin снимаем у удалённых, чтобы не блокировать пересчёт
    offer.pinned = False
    apply_weights(stream)
    mark_draft(stream)
    db.commit()
    return get_stream_or_404(db, stream.id)


def bring_back_offer(db: Session, stream: Stream, offer_row_id: int) -> Stream:
    offer = next((o for o in stream.offers if o.id == offer_row_id), None)
    if not offer:
        raise LookupError("Оффер не найден в потоке")
    if offer.status == "active":
        return stream
    offer.status = "active"
    offer.pinned = False
    apply_weights(stream)
    mark_draft(stream)
    db.commit()
    return get_stream_or_404(db, stream.id)


def patch_offer(
    db: Session,
    stream: Stream,
    offer_row_id: int,
    *,
    pinned: bool | None = None,
    share: int | None = None,
) -> Stream:
    offer = next((o for o in stream.offers if o.id == offer_row_id), None)
    if not offer:
        raise LookupError("Оффер не найден в потоке")
    if offer.status != "active":
        raise ValueError("Нельзя менять удалённый оффер — сначала BRING BACK")

    before = [
        (o.keitaro_offer_id, o.share, o.status, o.pinned) for o in stream.offers
    ]
    items = _to_weights(stream)

    try:
        if pinned is True:
            target = next(i for i in items if i.id == offer_row_id)
            target.pinned = True
            validate_pinned_state(items)
            redistribute(items)
        elif pinned is False:
            target = next(i for i in items if i.id == offer_row_id)
            target.pinned = False
            redistribute(items)

        if share is not None:
            # актуальные pin после возможного toggle выше
            _from_weights(stream, items)
            items = _to_weights(stream)
            target = next(i for i in items if i.id == offer_row_id)
            if target.pinned:
                set_pinned_share(items, offer_row_id, share)
            else:
                set_unpinned_share(items, offer_row_id, share)

        _from_weights(stream, items)
    except WeightError:
        db.rollback()
        raise

    after = [
        (o.keitaro_offer_id, o.share, o.status, o.pinned) for o in stream.offers
    ]
    if before != after:
        mark_draft(stream)
    elif not stream.is_draft:
        stream.published_snapshot = {"offers": _snapshot_offers(stream)}

    db.commit()
    return get_stream_or_404(db, stream.id)


def _recreate_offers_stream_in_keitaro(
    stream: Stream,
    client: KeitaroClient,
    payload_offers: list[dict[str, Any]],
) -> int:
    """Поток с офферами удалён в KT, кампания жива → создаём его заново, возвращаем новый id."""
    kt_campaign_id = stream.campaign.keitaro_campaign_id
    try:
        client.get_campaign(kt_campaign_id)
    except KeitaroError as exc:
        if exc.status_code == 404:
            raise CampaignMissingInKeitaroError(
                campaign_id=stream.campaign_id,
                keitaro_campaign_id=kt_campaign_id,
            ) from exc
        raise

    created = client.create_stream(
        {
            "campaign_id": kt_campaign_id,
            "name": stream.name,
            "type": stream.type or "default",
            "schema": stream.stream_schema or "landings",
            "offers": payload_offers,
        }
    )
    return int(created["id"])


def push_stream_to_keitaro(db: Session, stream: Stream, client: KeitaroClient) -> Stream:
    campaign_id = stream.campaign_id
    stream_id = stream.id
    if not stream.has_offers:
        stream.is_draft = False
        write_sync_log(
            db,
            campaign_id=campaign_id,
            stream_id=stream_id,
            action="push",
            status="ok",
            summary=format_push_summary(stream, []),
        )
        db.commit()
        return stream

    active = sorted(
        (o for o in stream.offers if o.status == "active"), key=lambda x: x.sort_order
    )
    recreated = False
    try:
        total = sum(o.share for o in active)
        if active and (total != 100 or any(o.share < MIN_ACTIVE_SHARE for o in active)):
            apply_weights(stream)
            total = sum(o.share for o in active)
            if total != 100:
                raise WeightError(f"Сумма долей активных офферов = {total}%, нужно 100%")

        payload_offers = [
            {
                "offer_id": o.keitaro_offer_id,
                "share": o.share,
                "state": "active",
            }
            for o in active
        ]
        try:
            client.update_stream(stream.keitaro_stream_id, {"offers": payload_offers})
        except KeitaroError as exc:
            if exc.status_code != 404:
                raise
            stream.keitaro_stream_id = _recreate_offers_stream_in_keitaro(
                stream, client, payload_offers
            )
            recreated = True
    except (KeitaroError, WeightError, CampaignMissingInKeitaroError) as exc:
        _log_error(db, campaign_id=campaign_id, stream_id=stream_id, action="push", exc=exc)
        raise

    save_published_snapshot(stream)
    summary = format_push_summary(stream, active)
    if recreated:
        summary += " · поток пересоздан в KT"
    write_sync_log(
        db,
        campaign_id=campaign_id,
        stream_id=stream_id,
        action="push",
        status="ok",
        summary=summary,
    )
    db.commit()
    return get_stream_or_404(db, stream_id)


def _restore_from_snapshot(stream: Stream) -> bool:
    """Возвращает офферы потока к published_snapshot и снимает draft. False — снапшота нет."""
    stream.is_draft = False
    snap = stream.published_snapshot
    if not snap or "offers" not in snap:
        return False

    desired = {item["keitaro_offer_id"]: item for item in snap["offers"]}
    local_by_id = {o.keitaro_offer_id: o for o in stream.offers}

    for offer_id, item in desired.items():
        local = local_by_id.get(offer_id)
        if local is None:
            stream.offers.append(
                StreamOffer(
                    keitaro_offer_id=offer_id,
                    offer_name=item.get("offer_name") or f"Offer {offer_id}",
                    share=int(item.get("share") or 0),
                    pinned=bool(item.get("pinned")),
                    status=item.get("status") or "active",
                    sort_order=int(item.get("sort_order") or 0),
                )
            )
        else:
            local.offer_name = item.get("offer_name") or local.offer_name
            local.share = int(item.get("share") or 0)
            local.pinned = bool(item.get("pinned"))
            local.status = item.get("status") or "active"
            local.sort_order = int(item.get("sort_order") or 0)

    for local in list(stream.offers):
        if local.keitaro_offer_id not in desired:
            stream.offers.remove(local)
    return True


def cancel_stream_draft(db: Session, stream: Stream) -> Stream:
    campaign_id = stream.campaign_id
    stream_id = stream.id
    diff = build_stream_diff(stream) if stream.is_draft else None
    restored = _restore_from_snapshot(stream)
    write_sync_log(
        db,
        campaign_id=campaign_id,
        stream_id=stream_id,
        action="cancel",
        status="ok",
        summary=format_cancel_summary(stream, restored=restored, diff=diff),
    )
    db.commit()
    return get_stream_or_404(db, stream_id)


def _extract_geo_from_streams(kt_streams: list[dict]) -> str:
    for kt in kt_streams:
        for f in kt.get("filters") or []:
            if str(f.get("name", "")).lower() == "country":
                payload = f.get("payload") or []
                if isinstance(payload, list) and payload:
                    return ",".join(str(x).upper() for x in payload)
                if isinstance(payload, str) and payload.strip():
                    return payload.strip().upper()
    return ""


def delete_local_campaign(db: Session, campaign: Campaign) -> None:
    """Удаляет только локальную копию (KT не трогаем — её уже может не быть)."""
    db.delete(campaign)
    db.commit()


def delete_campaign_with_keitaro(
    db: Session, campaign: Campaign, client: KeitaroClient
) -> None:
    """
    Удаляет кампанию в Keitaro, затем локально.
    Если в KT уже 404 — считаем удалённой и всё равно чистим Wrapper.
    """
    kt_id = campaign.keitaro_campaign_id
    try:
        client.delete_campaign(kt_id)
    except KeitaroError as exc:
        if exc.status_code != 404:
            raise
    delete_local_campaign(db, campaign)


KT_CAMPAIGNS_PAGE = 200


def _list_all_kt_campaigns(client: KeitaroClient) -> list[dict]:
    # пропавшие в KT кампании удаляются локально, поэтому список нужен целиком, а не первая страница
    rows: list[dict] = []
    seen: set[int] = set()
    offset = 0
    while True:
        page = client.list_campaigns(offset=offset, limit=KT_CAMPAIGNS_PAGE)
        fresh = [
            r for r in page if r.get("id") is not None and int(r["id"]) not in seen
        ]
        rows.extend(fresh)
        seen.update(int(r["id"]) for r in fresh)
        # меньше страницы или одни повторы (API игнорирует offset) — список закончился
        if len(page) < KT_CAMPAIGNS_PAGE or not fresh:
            return rows
        offset += KT_CAMPAIGNS_PAGE


def sync_campaigns_from_keitaro(db: Session, client: KeitaroClient) -> dict[str, int | str]:
    """
    Подтягивает кампании из Keitaro в локальную БД.
    Новые — импорт + потоки; уже известные — только имя;
    пропавшие в KT — удаляются локально (включая draft-«призраков»).
    """
    kt_campaigns = _list_all_kt_campaigns(client)
    kt_ids = {int(row["id"]) for row in kt_campaigns}
    existing = {
        c.keitaro_campaign_id: c
        for c in db.query(Campaign).all()
    }

    imported = 0
    refreshed = 0

    for row in kt_campaigns:
        kid = int(row["id"])
        name = str(row.get("name") or f"Campaign {kid}")
        local = existing.get(kid)

        if local is None:
            local = Campaign(keitaro_campaign_id=kid, name=name, geo="")
            db.add(local)
            db.flush()
            db.commit()
            try:
                local = get_campaign_or_404(db, local.id)
                fetch_streams_from_keitaro(db, local, client)
            except (KeitaroError, CampaignMissingInKeitaroError):
                # ошибка уже записана в журнал кампании; импорт остальных продолжаем
                pass
            imported += 1
            existing[kid] = local
        elif local.name != name:
            local.name = name
            db.commit()
            refreshed += 1

    removed = 0
    for kid, local in existing.items():
        if kid not in kt_ids:
            db.delete(local)
            removed += 1
    if removed:
        db.commit()

    total = db.query(Campaign).count()
    parts = [f"Новых кампаний: {imported}", f"обновлено имён: {refreshed}"]
    if removed:
        parts.append(f"удалено локально (нет в KT): {removed}")
    return {
        "imported": imported,
        "refreshed": refreshed,
        "removed": removed,
        "total_local": total,
        "message": ", ".join(parts),
    }
