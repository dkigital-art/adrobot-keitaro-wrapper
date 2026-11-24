from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.api.errors import campaign_missing_http_error, keitaro_http_error
from app.db import get_db
from app.models import Campaign, SyncLog
from app.schemas import (
    CampaignCreate,
    CampaignListItem,
    CampaignOut,
    MetaOut,
    SyncFromKtResult,
    SyncLogOut,
    serialize_campaign,
)
from app.config import get_settings
from app.services.campaigns import create_campaign_with_streams
from app.services.keitaro import KeitaroClient, KeitaroError
from app.services.sync import (
    CampaignMissingInKeitaroError,
    DraftConflictError,
    delete_campaign_with_keitaro,
    fetch_streams_from_keitaro,
    get_campaign_or_404,
    sync_campaigns_from_keitaro,
)

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])


def build_meta() -> MetaOut:
    settings = get_settings()
    domain_id = settings.keitaro_domain_id
    group_id = settings.keitaro_group_id
    ts_id = settings.keitaro_traffic_source_id
    domain_name = settings.keitaro_domain_name or None
    group_name = settings.keitaro_group_name or "FORTESTS"
    ts_name = settings.keitaro_traffic_source_name or "FORTESTS"

    ids_ready = bool(domain_id and group_id and ts_id)
    if settings.keitaro_api_key and not ids_ready:
        try:
            with KeitaroClient(settings) as client:
                defaults = client.resolve_defaults()
            domain_id = defaults.get("domain_id")
            group_id = defaults.get("group_id")
            ts_id = defaults.get("traffic_source_id")
            domain_name = defaults.get("domain_name") or domain_name
            group_name = defaults.get("group_name") or group_name
            ts_name = defaults.get("traffic_source_name") or ts_name
        except KeitaroError:
            pass
    elif not domain_name and domain_id:
        domain_name = f"domain #{domain_id}"

    return MetaOut(
        keitaro_url=settings.keitaro_url,
        domain_id=domain_id,
        group_id=group_id,
        traffic_source_id=ts_id,
        domain_name=domain_name,
        group_name=group_name,
        traffic_source_name=ts_name,
        api_key_configured=bool(settings.keitaro_api_key),
    )


@router.post("/sync-from-kt", response_model=SyncFromKtResult)
def sync_from_kt(db: Session = Depends(get_db)):
    """Импорт новых кампаний из Keitaro (созданных напрямую в админке)."""
    try:
        with KeitaroClient() as client:
            result = sync_campaigns_from_keitaro(db, client)
        return SyncFromKtResult(**result)
    except KeitaroError as exc:
        raise keitaro_http_error(exc) from exc


@router.get("", response_model=list[CampaignListItem])
def list_campaigns(db: Session = Depends(get_db)):
    rows = (
        db.query(Campaign)
        .options(joinedload(Campaign.streams))
        .order_by(Campaign.id.desc())
        .all()
    )
    return [
        CampaignListItem(
            id=c.id,
            keitaro_campaign_id=c.keitaro_campaign_id,
            name=c.name,
            geo=c.geo,
            created_at=c.created_at,
            streams_count=len(c.streams),
            has_draft=any(s.is_draft for s in c.streams),
        )
        for c in rows
    ]


@router.post("", response_model=CampaignOut, status_code=201)
def create_campaign(body: CampaignCreate, db: Session = Depends(get_db)):
    try:
        with KeitaroClient() as client:
            campaign = create_campaign_with_streams(
                db,
                name=body.name,
                geo=body.geo,
                offer_id=body.offer_id,
                client=client,
            )
        return serialize_campaign(campaign)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeitaroError as exc:
        raise keitaro_http_error(exc) from exc


@router.get("/{campaign_id}/logs", response_model=list[SyncLogOut])
def campaign_logs(
    campaign_id: int,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
):
    try:
        get_campaign_or_404(db, campaign_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    rows = (
        db.query(SyncLog)
        .filter(SyncLog.campaign_id == campaign_id)
        .order_by(SyncLog.id.desc())
        .limit(limit)
        .all()
    )
    return rows


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: int, db: Session = Depends(get_db)):
    try:
        return serialize_campaign(get_campaign_or_404(db, campaign_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{campaign_id}", status_code=204)
def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    """Удаляет кампанию в Keitaro и локально. Если в KT уже нет — только локально."""
    try:
        campaign = get_campaign_or_404(db, campaign_id)
        with KeitaroClient() as client:
            delete_campaign_with_keitaro(db, campaign, client)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeitaroError as exc:
        raise keitaro_http_error(exc) from exc


@router.post("/{campaign_id}/fetch", response_model=CampaignOut)
def fetch_streams(
    campaign_id: int,
    force: bool = Query(False, description="Перезаписать draft-потоки данными из KT"),
    db: Session = Depends(get_db),
):
    try:
        campaign = get_campaign_or_404(db, campaign_id)
        with KeitaroClient() as client:
            updated = fetch_streams_from_keitaro(db, campaign, client, force=force)
        return serialize_campaign(updated)
    except DraftConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "draft_stream_ids": exc.draft_stream_ids,
                "code": "draft_conflict",
            },
        ) from exc
    except CampaignMissingInKeitaroError as exc:
        raise campaign_missing_http_error(exc) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except KeitaroError as exc:
        raise keitaro_http_error(exc) from exc
