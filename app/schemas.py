from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    geo: str = Field(min_length=1, max_length=255, description="Коды стран через запятую, напр. MX,AU,RO")
    offer_id: int = Field(gt=0)

    @field_validator("geo")
    @classmethod
    def normalize_geo(cls, value: str) -> str:
        parts = [p.strip().upper() for p in value.replace(";", ",").split(",") if p.strip()]
        if not parts:
            raise ValueError("Укажите хотя бы один geo-код")
        return ",".join(parts)


class OfferOut(BaseModel):
    id: int
    name: str
    group_id: int | None = None
    country: list[str] | None = None


class StreamOfferOut(BaseModel):
    id: int
    keitaro_offer_id: int
    offer_name: str
    share: int
    pinned: bool
    status: Literal["active", "removed"]
    sort_order: int

    model_config = {"from_attributes": True}


class DiffOfferOut(BaseModel):
    keitaro_offer_id: int
    offer_name: str
    share: int | None = None
    share_before: int | None = None
    share_after: int | None = None
    pinned: bool | None = None
    pinned_before: bool | None = None
    pinned_after: bool | None = None


class StreamDiffOut(BaseModel):
    added: list[DiffOfferOut] = []
    removed: list[DiffOfferOut] = []
    share_changed: list[DiffOfferOut] = []
    pin_changed: list[DiffOfferOut] = []
    empty: bool = False


class StreamOut(BaseModel):
    id: int
    keitaro_stream_id: int
    name: str
    type: str
    stream_schema: str = Field(
        validation_alias="stream_schema",
        serialization_alias="schema",
    )
    has_offers: bool
    is_draft: bool
    offers: list[StreamOfferOut] = []
    active_share_sum: int = 0
    diff: StreamDiffOut | None = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class SyncLogOut(BaseModel):
    id: int
    campaign_id: int
    stream_id: int | None = None
    action: str
    status: str
    summary: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CampaignOut(BaseModel):
    id: int
    keitaro_campaign_id: int
    name: str
    geo: str
    created_at: datetime
    streams: list[StreamOut] = []

    model_config = {"from_attributes": True}


class CampaignListItem(BaseModel):
    id: int
    keitaro_campaign_id: int
    name: str
    geo: str
    created_at: datetime
    streams_count: int = 0
    has_draft: bool = False

    model_config = {"from_attributes": True}


class OfferAddRequest(BaseModel):
    offer_id: int = Field(gt=0)


class OfferPatchRequest(BaseModel):
    pinned: bool | None = None
    share: int | None = Field(default=None, ge=0, le=100)


class MetaOut(BaseModel):
    keitaro_url: str
    domain_id: int | None
    group_id: int | None
    traffic_source_id: int | None
    domain_name: str | None = None
    group_name: str | None = None
    traffic_source_name: str | None = None
    api_key_configured: bool


class SyncFromKtResult(BaseModel):
    imported: int
    refreshed: int
    removed: int = 0
    total_local: int
    message: str


def serialize_stream(stream) -> StreamOut:
    from app.services.diff import build_stream_diff

    offers = [
        StreamOfferOut.model_validate(o)
        for o in sorted(stream.offers or [], key=lambda x: x.sort_order)
    ]
    active_sum = sum(o.share for o in offers if o.status == "active")
    diff_raw = build_stream_diff(stream) if stream.is_draft else None
    diff = StreamDiffOut.model_validate(diff_raw) if diff_raw else None
    return StreamOut(
        id=stream.id,
        keitaro_stream_id=stream.keitaro_stream_id,
        name=stream.name,
        type=stream.type,
        stream_schema=stream.stream_schema,
        has_offers=stream.has_offers,
        is_draft=stream.is_draft,
        offers=offers,
        active_share_sum=active_sum,
        diff=diff,
    )


def serialize_campaign(campaign) -> CampaignOut:
    return CampaignOut(
        id=campaign.id,
        keitaro_campaign_id=campaign.keitaro_campaign_id,
        name=campaign.name,
        geo=campaign.geo,
        created_at=campaign.created_at,
        streams=[serialize_stream(s) for s in campaign.streams],
    )
