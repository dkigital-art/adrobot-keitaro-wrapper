"""Компактные pytest-сценарии: веса/pin, CANCEL, FETCH 409, SyncLog, удаление в KT.

Keitaro замокан через FakeKeitaro — без реальных сетевых вызовов.
"""

from __future__ import annotations

import pytest

from app.models import Campaign, StreamOffer
from app.services.sync import (
    KT_CAMPAIGNS_PAGE,
    CampaignMissingInKeitaroError,
    DraftConflictError,
    add_offer_to_stream,
    cancel_stream_draft,
    delete_campaign_with_keitaro,
    fetch_streams_from_keitaro,
    get_campaign_or_404,
    patch_offer,
    push_stream_to_keitaro,
    remove_offer,
    sync_campaigns_from_keitaro,
)
from app.services.weights import OfferWeight, WeightError, redistribute, validate_pinned_state
from tests.conftest import offers_state, sync_logs


# --- unit: доли с pin и округлением до 100% ---


def test_pin_preserves_share_and_remainder_sums_to_100():
    offers = [
        OfferWeight(1, 40, True, "active"),
        OfferWeight(2, 0, False, "active"),
        OfferWeight(3, 0, False, "active"),
    ]
    redistribute(offers)
    assert [o.share for o in offers] == [40, 30, 30]


def test_uneven_split_rounds_to_exactly_100():
    offers = [OfferWeight(i, 0, False, "active") for i in (1, 2, 3)]
    redistribute(offers)
    assert [o.share for o in offers] == [34, 33, 33]


def test_pinned_overflow_rejected():
    offers = [
        OfferWeight(1, 60, True, "active"),
        OfferWeight(2, 50, True, "active"),
    ]
    with pytest.raises(WeightError, match="превышает 100%"):
        redistribute(offers)


def test_pinned_sum_100_blocks_extra_unpinned():
    offers = [
        OfferWeight(1, 100, True, "active"),
        OfferWeight(2, 0, False, "active"),
    ]
    with pytest.raises(WeightError, match="100%"):
        validate_pinned_state(offers)


def test_no_room_for_min_share_of_each_unpinned():
    offers = [
        OfferWeight(1, 98, True, "active"),
        OfferWeight(2, 0, False, "active"),
        OfferWeight(3, 0, False, "active"),
        OfferWeight(4, 0, False, "active"),
    ]
    with pytest.raises(WeightError, match="минимум 1%"):
        redistribute(offers)


# --- integration: CANCEL / FETCH / SyncLog ---


def test_cancel_restores_snapshot_and_writes_synclog(db, kt, published_two):
    campaign, stream = published_two
    before = offers_state(stream)
    row_a = next(o for o in stream.offers if o.keitaro_offer_id == 1)
    row_b = next(o for o in stream.offers if o.keitaro_offer_id == 2)

    stream = patch_offer(db, stream, row_a.id, pinned=True)
    stream = add_offer_to_stream(db, stream, 3, kt)
    stream = remove_offer(db, stream, row_b.id)
    assert stream.is_draft

    stream = cancel_stream_draft(db, stream)
    assert not stream.is_draft
    assert offers_state(stream) == before
    assert db.query(StreamOffer).filter(StreamOffer.keitaro_offer_id == 3).count() == 0
    logs = sync_logs(db, campaign.id, "cancel")
    assert [l.status for l in logs] == ["ok"]
    assert "откат (+1, −1, pin 1)" in logs[0].summary


def test_fetch_without_force_raises_draft_conflict(db, kt, campaign, offers_stream):
    add_offer_to_stream(db, offers_stream, 2, kt)
    with pytest.raises(DraftConflictError) as exc:
        fetch_streams_from_keitaro(db, get_campaign_or_404(db, campaign.id), kt)
    assert exc.value.draft_stream_ids == [offers_stream.id]


def test_fetch_marks_missing_kt_offer_removed_and_logs(db, kt, published_two):
    campaign, stream = published_two
    kt.streams[stream.keitaro_stream_id]["offers"] = [
        {"offer_id": 1, "share": 100, "state": "active"}
    ]
    campaign = fetch_streams_from_keitaro(db, get_campaign_or_404(db, campaign.id), kt)
    stream = next(s for s in campaign.streams if s.has_offers)
    assert offers_state(stream) == [
        (1, 100, False, "active"),
        (2, 0, False, "removed"),
    ]
    assert not stream.is_draft
    assert sync_logs(db, campaign.id, "fetch")[-1].status == "ok"


def test_fetch_http_409_and_force_ok(api_client):
    res = api_client.post("/api/campaigns", json={"name": "T", "geo": "AU", "offer_id": 1})
    assert res.status_code == 201, res.text
    campaign = res.json()
    stream = next(s for s in campaign["streams"] if s["has_offers"])

    res = api_client.post(f"/api/streams/{stream['id']}/offers", json={"offer_id": 2})
    assert res.status_code == 200
    assert res.json()["is_draft"]

    res = api_client.post(f"/api/campaigns/{campaign['id']}/fetch")
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["code"] == "draft_conflict"
    assert detail["draft_stream_ids"] == [stream["id"]]

    res = api_client.post(f"/api/campaigns/{campaign['id']}/fetch?force=true")
    assert res.status_code == 200, res.text

    logs = api_client.get(f"/api/campaigns/{campaign['id']}/logs").json()
    assert (logs[0]["action"], logs[0]["status"]) == ("fetch", "ok")


# --- кампания или поток удалены в Keitaro ---


def test_list_fetch_removes_campaigns_missing_in_kt(db, kt, campaign):
    kt.delete_campaign(campaign.keitaro_campaign_id)
    result = sync_campaigns_from_keitaro(db, kt)
    assert result["removed"] == 1
    assert db.query(Campaign).filter(Campaign.id == campaign.id).count() == 0


def test_list_fetch_reads_all_pages_before_removing(db, kt, campaign):
    for kid in range(1000, 1000 + KT_CAMPAIGNS_PAGE + 5):
        kt.campaigns[kid] = {"id": kid, "name": f"bulk {kid}"}
    # наша кампания — последняя в списке KT, на второй странице
    own = kt.campaigns.pop(campaign.keitaro_campaign_id)
    kt.campaigns[own["id"]] = own

    result = sync_campaigns_from_keitaro(db, kt)
    assert result["removed"] == 0
    assert db.query(Campaign).filter(Campaign.id == campaign.id).count() == 1


def test_campaign_fetch_missing_in_kt_is_logged(db, kt, campaign):
    kt.delete_campaign(campaign.keitaro_campaign_id)
    with pytest.raises(CampaignMissingInKeitaroError):
        fetch_streams_from_keitaro(db, get_campaign_or_404(db, campaign.id), kt)
    assert sync_logs(db, campaign.id, "fetch")[-1].status == "error"


def test_push_recreates_stream_when_missing_in_kt(db, kt, published_two):
    campaign, stream = published_two
    old_sid = stream.keitaro_stream_id
    del kt.streams[old_sid]

    stream = add_offer_to_stream(db, stream, 3, kt)
    stream = push_stream_to_keitaro(db, stream, kt)

    assert stream.keitaro_stream_id != old_sid
    assert not stream.is_draft
    kt_offers = kt.streams[stream.keitaro_stream_id]["offers"]
    assert sorted(o["offer_id"] for o in kt_offers) == [1, 2, 3]
    assert sum(o["share"] for o in kt_offers) == 100
    assert "пересоздан" in sync_logs(db, campaign.id, "push")[-1].summary


def test_push_410_when_campaign_missing_in_kt(api_client, kt):
    campaign = api_client.post(
        "/api/campaigns", json={"name": "T", "geo": "AU", "offer_id": 1}
    ).json()
    stream = next(s for s in campaign["streams"] if s["has_offers"])
    api_client.post(f"/api/streams/{stream['id']}/offers", json={"offer_id": 2})
    kt.delete_campaign(campaign["keitaro_campaign_id"])

    res = api_client.post(f"/api/streams/{stream['id']}/push")
    assert res.status_code == 410
    assert res.json()["detail"]["code"] == "campaign_missing_in_kt"
    assert api_client.get(f"/api/streams/{stream['id']}").json()["is_draft"]


# --- удаление кампании ---


def test_delete_campaign_removes_from_kt_and_local(db, kt, campaign):
    kid = campaign.keitaro_campaign_id
    local_id = campaign.id
    delete_campaign_with_keitaro(db, campaign, kt)
    assert kid in kt.deleted_campaigns
    assert kid not in kt.campaigns
    assert db.query(Campaign).filter(Campaign.id == local_id).count() == 0


def test_delete_campaign_ok_if_already_gone_in_kt(db, kt, campaign):
    local_id = campaign.id
    kt.delete_campaign(campaign.keitaro_campaign_id)
    delete_campaign_with_keitaro(db, get_campaign_or_404(db, local_id), kt)
    assert db.query(Campaign).filter(Campaign.id == local_id).count() == 0


def test_delete_campaign_keeps_local_on_kt_error(api_client, kt):
    campaign = api_client.post(
        "/api/campaigns", json={"name": "T", "geo": "AU", "offer_id": 1}
    ).json()
    kt.fail.add("delete_campaign")

    res = api_client.delete(f"/api/campaigns/{campaign['id']}")
    assert res.status_code == 502
    assert api_client.get(f"/api/campaigns/{campaign['id']}").status_code == 200

    kt.fail.discard("delete_campaign")
    assert api_client.delete(f"/api/campaigns/{campaign['id']}").status_code == 204
    assert api_client.get(f"/api/campaigns/{campaign['id']}").status_code == 404
