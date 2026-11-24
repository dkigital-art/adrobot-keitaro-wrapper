"""Создание кампании в Keitaro + двух потоков + локальная запись."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models import Campaign, Stream, StreamOffer
from app.services.keitaro import KeitaroClient, KeitaroError
from app.services.logging_sync import write_sync_log
from app.services.sync import get_campaign_or_404, save_published_snapshot

logger = logging.getLogger(__name__)


def parse_geo_codes(geo: str) -> list[str]:
    return [p.strip().upper() for p in geo.replace(";", ",").split(",") if p.strip()]


def create_campaign_with_streams(
    db: Session,
    *,
    name: str,
    geo: str,
    offer_id: int,
    client: KeitaroClient,
) -> Campaign:
    geo_codes = parse_geo_codes(geo)
    if not geo_codes:
        raise ValueError("Geo пустой")

    defaults = client.resolve_defaults()
    if not defaults.get("domain_id"):
        raise KeitaroError("Не удалось определить domain_id (KEITARO_DOMAIN_ID)")
    if not defaults.get("group_id"):
        raise KeitaroError("Не удалось определить group_id (KEITARO_GROUP_ID)")
    if not defaults.get("traffic_source_id"):
        raise KeitaroError(
            "Не удалось определить traffic_source_id (KEITARO_TRAFFIC_SOURCE_ID)"
        )

    try:
        offer = client.get_offer(offer_id)
    except KeitaroError as exc:
        if exc.status_code == 404:
            raise ValueError(f"Оффер {offer_id} не найден в Keitaro") from exc
        raise
    offer_name = offer.get("name") or f"Offer {offer_id}"

    campaign_payload: dict = {
        "name": name,
        "group_id": defaults["group_id"],
        "domain_id": defaults["domain_id"],
        "traffic_source_id": defaults["traffic_source_id"],
    }
    kt_campaign = client.create_campaign(campaign_payload)
    kt_campaign_id = int(kt_campaign["id"])

    try:
        local_id = _create_streams_and_local(
            db,
            client=client,
            name=name,
            geo_codes=geo_codes,
            offer_id=offer_id,
            offer_name=offer_name,
            kt_campaign_id=kt_campaign_id,
        )
    except Exception as exc:
        db.rollback()
        note = _discard_kt_campaign(client, kt_campaign_id)
        if isinstance(exc, KeitaroError):
            raise KeitaroError(
                f"{exc}. {note}", status_code=exc.status_code, body=exc.body
            ) from exc
        raise
    return get_campaign_or_404(db, local_id)


def _discard_kt_campaign(client: KeitaroClient, kt_campaign_id: int) -> str:
    """Удаляет недособранную кампанию в Keitaro, чтобы не оставлять сирот."""
    try:
        client.delete_campaign(kt_campaign_id)
        return f"Неполная кампания KT #{kt_campaign_id} удалена."
    except KeitaroError as exc:
        logger.error("Не удалось удалить кампанию KT #%s: %s", kt_campaign_id, exc)
        return f"Не удалось удалить неполную кампанию KT #{kt_campaign_id} — удалите её вручную."


def _create_streams_and_local(
    db: Session,
    *,
    client: KeitaroClient,
    name: str,
    geo_codes: list[str],
    offer_id: int,
    offer_name: str,
    kt_campaign_id: int,
) -> int:
    # Поток 1: geo → Google
    geo_label = ",".join(geo_codes)
    stream_geo_payload = {
        "campaign_id": kt_campaign_id,
        "name": f"Geo → Google ({geo_label})",
        "type": "regular",
        "schema": "redirect",
        "action_type": "http",
        "action_payload": "https://google.com",
        "filters": [
            {
                "name": "country",
                "mode": "accept",
                "payload": geo_codes,
            }
        ],
    }
    kt_stream_geo = client.create_stream(stream_geo_payload)

    # Поток 2: офферы
    stream_offers_payload = {
        "campaign_id": kt_campaign_id,
        "name": "Offers",
        "type": "default",
        "schema": "landings",
        "offers": [
            {
                "offer_id": offer_id,
                "share": 100,
                "state": "active",
            }
        ],
    }
    kt_stream_offers = client.create_stream(stream_offers_payload)

    local = Campaign(
        keitaro_campaign_id=kt_campaign_id,
        name=name,
        geo=geo_label,
    )
    db.add(local)
    db.flush()

    stream1 = Stream(
        campaign_id=local.id,
        keitaro_stream_id=int(kt_stream_geo["id"]),
        name=kt_stream_geo.get("name") or stream_geo_payload["name"],
        type="regular",
        stream_schema="redirect",
        has_offers=False,
        is_draft=False,
        published_snapshot={"offers": []},
    )
    stream2 = Stream(
        campaign_id=local.id,
        keitaro_stream_id=int(kt_stream_offers["id"]),
        name=kt_stream_offers.get("name") or "Offers",
        type="default",
        stream_schema="landings",
        has_offers=True,
        is_draft=False,
    )
    db.add_all([stream1, stream2])
    db.flush()

    stream2.offers.append(
        StreamOffer(
            keitaro_offer_id=offer_id,
            offer_name=offer_name,
            share=100,
            pinned=False,
            status="active",
            sort_order=0,
        )
    )
    save_published_snapshot(stream2)

    write_sync_log(
        db,
        campaign_id=local.id,
        action="create_campaign",
        status="ok",
        summary=f"Создана кампания KT #{kt_campaign_id}, geo={geo_label}, offer={offer_id}",
    )
    db.commit()
    return local.id
