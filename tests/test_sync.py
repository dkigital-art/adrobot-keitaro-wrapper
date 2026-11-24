"""Сервисные тесты жизненного цикла draft / snapshot / PUSH / CANCEL / FETCH и SyncLog.

БД — SQLite в памяти, Keitaro — заглушка FakeKeitaro (без сети).
"""

import copy
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401  (регистрирует таблицы в Base.metadata)
from app.db import Base
from app.models import Campaign, StreamOffer, SyncLog
from app.services.campaigns import create_campaign_with_streams
from app.services.keitaro import KeitaroError
from app.services.sync import (
    DraftConflictError,
    add_offer_to_stream,
    cancel_stream_draft,
    fetch_streams_from_keitaro,
    get_campaign_or_404,
    get_stream_or_404,
    patch_offer,
    push_stream_to_keitaro,
    remove_offer,
)
from tests.fakes import FakeKeitaro


def offers_state(stream):
    return sorted(
        (o.keitaro_offer_id, o.share, o.pinned, o.status)
        for o in stream.offers
    )


class SyncLifecycleTest(unittest.TestCase):
    def setUp(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.engine = engine
        self.db = sessionmaker(bind=engine, autoflush=False)()
        self.kt = FakeKeitaro()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    # --- helpers ---
    def create(self):
        return create_campaign_with_streams(
            self.db, name="Test", geo="au, nz", offer_id=1, client=self.kt
        )

    def offers_stream(self, campaign):
        return get_stream_or_404(
            self.db, next(s.id for s in campaign.streams if s.has_offers)
        )

    def logs(self, campaign_id, action=None):
        q = self.db.query(SyncLog).filter(SyncLog.campaign_id == campaign_id)
        if action:
            q = q.filter(SyncLog.action == action)
        return q.order_by(SyncLog.id).all()

    def published_two_offers(self):
        campaign = self.create()
        stream = add_offer_to_stream(self.db, self.offers_stream(campaign), 2, self.kt)
        stream = push_stream_to_keitaro(self.db, stream, self.kt)
        return campaign, stream

    # --- create ---
    def test_create_streams_not_draft_and_snapshot_matches(self):
        campaign = self.create()
        self.assertEqual(len(campaign.streams), 2)
        self.assertTrue(all(not s.is_draft for s in campaign.streams))
        self.assertEqual(campaign.geo, "AU,NZ")

        stream = self.offers_stream(campaign)
        self.assertEqual(offers_state(stream), [(1, 100, False, "active")])
        snap = stream.published_snapshot["offers"]
        self.assertEqual(
            [(o["keitaro_offer_id"], o["share"], o["pinned"], o["status"]) for o in snap],
            [(1, 100, False, "active")],
        )
        self.assertEqual([l.status for l in self.logs(campaign.id, "create_campaign")], ["ok"])

    def test_create_partial_failure_removes_kt_campaign(self):
        self.kt.fail.add("create_offers_stream")
        with self.assertRaises(KeitaroError) as ctx:
            self.create()
        self.assertEqual(len(self.kt.deleted_campaigns), 1)
        self.assertIn("удалена", str(ctx.exception))
        self.assertEqual(self.db.query(Campaign).count(), 0)

    def test_create_unknown_offer_is_value_error(self):
        with self.assertRaises(ValueError):
            create_campaign_with_streams(
                self.db, name="X", geo="AU", offer_id=999, client=self.kt
            )
        self.assertEqual(self.kt.campaigns, {})

    # --- push ---
    def test_push_sends_active_offers_updates_snapshot_and_logs(self):
        campaign, stream = self.published_two_offers()
        self.assertFalse(stream.is_draft)
        kt_offers = self.kt.streams[stream.keitaro_stream_id]["offers"]
        self.assertEqual(
            sorted((o["offer_id"], o["share"]) for o in kt_offers), [(1, 50), (2, 50)]
        )
        snap = stream.published_snapshot["offers"]
        self.assertEqual(
            sorted((o["keitaro_offer_id"], o["share"]) for o in snap), [(1, 50), (2, 50)]
        )
        push_log = self.logs(campaign.id, "push")[-1]
        self.assertEqual(push_log.status, "ok")
        self.assertIn("1 Offer A 50%", push_log.summary)
        self.assertIn("2 Offer B 50%", push_log.summary)

    def test_push_excludes_removed_offers(self):
        campaign, stream = self.published_two_offers()
        row_b = next(o for o in stream.offers if o.keitaro_offer_id == 2)
        stream = remove_offer(self.db, stream, row_b.id)
        stream = push_stream_to_keitaro(self.db, stream, self.kt)
        kt_offers = self.kt.streams[stream.keitaro_stream_id]["offers"]
        self.assertEqual([(o["offer_id"], o["share"]) for o in kt_offers], [(1, 100)])

    def test_push_error_rolls_back_and_logs(self):
        campaign = self.create()
        stream = add_offer_to_stream(self.db, self.offers_stream(campaign), 2, self.kt)
        snapshot_before = copy.deepcopy(stream.published_snapshot)
        self.kt.fail.add("update_stream")

        with self.assertRaises(KeitaroError):
            push_stream_to_keitaro(self.db, stream, self.kt)

        stream = get_stream_or_404(self.db, stream.id)
        self.assertTrue(stream.is_draft)
        self.assertEqual(stream.published_snapshot, snapshot_before)
        errors = [l for l in self.logs(campaign.id, "push") if l.status == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("boom", errors[0].summary)

    # --- cancel ---
    def test_cancel_restores_share_pin_status_and_draft(self):
        campaign, stream = self.published_two_offers()
        before = offers_state(stream)
        row_a = next(o for o in stream.offers if o.keitaro_offer_id == 1)
        row_b = next(o for o in stream.offers if o.keitaro_offer_id == 2)

        stream = patch_offer(self.db, stream, row_a.id, pinned=True)
        stream = add_offer_to_stream(self.db, stream, 3, self.kt)
        stream = remove_offer(self.db, stream, row_b.id)
        self.assertTrue(stream.is_draft)

        stream = cancel_stream_draft(self.db, stream)
        self.assertFalse(stream.is_draft)
        self.assertEqual(offers_state(stream), before)
        self.assertEqual(
            self.db.query(StreamOffer).filter(StreamOffer.keitaro_offer_id == 3).count(), 0
        )
        self.assertEqual([l.status for l in self.logs(campaign.id, "cancel")], ["ok"])

    # --- fetch ---
    def test_fetch_with_draft_requires_force(self):
        campaign = self.create()
        add_offer_to_stream(self.db, self.offers_stream(campaign), 2, self.kt)
        campaign = get_campaign_or_404(self.db, campaign.id)
        with self.assertRaises(DraftConflictError) as ctx:
            fetch_streams_from_keitaro(self.db, campaign, self.kt)
        self.assertEqual(len(ctx.exception.draft_stream_ids), 1)

    def test_fetch_marks_offer_deleted_in_kt_as_removed(self):
        campaign, stream = self.published_two_offers()
        self.kt.streams[stream.keitaro_stream_id]["offers"] = [
            {"offer_id": 1, "share": 100, "state": "active"}
        ]
        campaign = fetch_streams_from_keitaro(
            self.db, get_campaign_or_404(self.db, campaign.id), self.kt
        )
        stream = self.offers_stream(campaign)
        self.assertEqual(
            offers_state(stream), [(1, 100, False, "active"), (2, 0, False, "removed")]
        )
        self.assertFalse(stream.is_draft)

    def test_force_fetch_discards_draft_only_offers(self):
        campaign = self.create()
        add_offer_to_stream(self.db, self.offers_stream(campaign), 2, self.kt)
        campaign = fetch_streams_from_keitaro(
            self.db, get_campaign_or_404(self.db, campaign.id), self.kt, force=True
        )
        stream = self.offers_stream(campaign)
        self.assertEqual(offers_state(stream), [(1, 100, False, "active")])
        self.assertFalse(stream.is_draft)
        self.assertIn("черновик перезаписан", self.logs(campaign.id, "fetch")[-1].summary)

    def test_fetch_deletes_streams_missing_in_kt(self):
        campaign = self.create()
        geo_kt_id = next(s.keitaro_stream_id for s in campaign.streams if not s.has_offers)
        del self.kt.streams[geo_kt_id]
        campaign = fetch_streams_from_keitaro(
            self.db, get_campaign_or_404(self.db, campaign.id), self.kt
        )
        self.assertEqual([s.has_offers for s in campaign.streams], [True])
        self.assertIn("удалено", self.logs(campaign.id, "fetch")[-1].summary)

    def test_fetch_error_keeps_local_state_and_logs(self):
        campaign, stream = self.published_two_offers()
        before = offers_state(stream)
        self.kt.fail.add("get_stream")
        with self.assertRaises(KeitaroError):
            fetch_streams_from_keitaro(
                self.db, get_campaign_or_404(self.db, campaign.id), self.kt
            )
        stream = get_stream_or_404(self.db, stream.id)
        self.assertEqual(offers_state(stream), before)
        self.assertEqual(self.logs(campaign.id, "fetch")[-1].status, "error")


class ApiErrorsTest(unittest.TestCase):
    """HTTP-контракт: 409 для draft-конфликта и 502 для ошибок Keitaro."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from app.db import get_db
        from app.main import app

        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        self.Session = sessionmaker(bind=engine, autoflush=False)
        self.kt = FakeKeitaro()

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        self.app = app
        app.dependency_overrides[get_db] = override_db
        self.client = TestClient(app)
        patcher_c = mock.patch("app.api.campaigns.KeitaroClient", return_value=self.kt)
        patcher_s = mock.patch("app.api.streams.KeitaroClient", return_value=self.kt)
        patcher_c.start()
        patcher_s.start()
        self.addCleanup(patcher_c.stop)
        self.addCleanup(patcher_s.stop)
        self.addCleanup(app.dependency_overrides.clear)

    def create(self):
        res = self.client.post(
            "/api/campaigns", json={"name": "T", "geo": "AU", "offer_id": 1}
        )
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()

    def test_fetch_conflict_409_detail(self):
        campaign = self.create()
        stream = next(s for s in campaign["streams"] if s["has_offers"])
        res = self.client.post(f"/api/streams/{stream['id']}/offers", json={"offer_id": 2})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["is_draft"])

        res = self.client.post(f"/api/campaigns/{campaign['id']}/fetch")
        self.assertEqual(res.status_code, 409)
        detail = res.json()["detail"]
        self.assertEqual(detail["code"], "draft_conflict")
        self.assertEqual(detail["draft_stream_ids"], [stream["id"]])

        res = self.client.post(f"/api/campaigns/{campaign['id']}/fetch?force=true")
        self.assertEqual(res.status_code, 200, res.text)

    def test_keitaro_error_is_502_and_logged(self):
        campaign = self.create()
        stream = next(s for s in campaign["streams"] if s["has_offers"])
        self.client.post(f"/api/streams/{stream['id']}/offers", json={"offer_id": 2})
        self.kt.fail.add("update_stream")

        res = self.client.post(f"/api/streams/{stream['id']}/push")
        self.assertEqual(res.status_code, 502)
        self.assertIn("boom", res.json()["detail"])

        logs = self.client.get(f"/api/campaigns/{campaign['id']}/logs").json()
        self.assertEqual((logs[0]["action"], logs[0]["status"]), ("push", "error"))

    def test_share_overflow_is_400(self):
        campaign = self.create()
        stream = next(s for s in campaign["streams"] if s["has_offers"])
        res = self.client.post(f"/api/streams/{stream['id']}/offers", json={"offer_id": 2})
        row = res.json()["offers"][0]
        res = self.client.patch(
            f"/api/streams/{stream['id']}/offers/{row['id']}", json={"share": 100}
        )
        self.assertEqual(res.status_code, 400)
        self.assertIn("минимум 1%", res.json()["detail"])


if __name__ == "__main__":
    unittest.main()
