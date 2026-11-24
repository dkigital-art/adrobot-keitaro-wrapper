"""Заглушка Keitaro Admin API для тестов (без сети)."""

from __future__ import annotations

import copy

from app.services.keitaro import KeitaroError


class FakeKeitaro:
    def __init__(self):
        self.offers = {
            1: {"id": 1, "name": "Offer A"},
            2: {"id": 2, "name": "Offer B"},
            3: {"id": 3, "name": "Offer C"},
        }
        self.campaigns: dict[int, dict] = {}
        self.streams: dict[int, dict] = {}
        self.deleted_campaigns: list[int] = []
        self.fail: set[str] = set()
        self._next_id = 100

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _maybe_fail(self, name: str) -> None:
        if name in self.fail:
            raise KeitaroError(f"Keitaro {name} → 500: boom", status_code=500)

    def resolve_defaults(self):
        return {"domain_id": 1, "group_id": 2, "traffic_source_id": 3}

    def get_offer(self, offer_id):
        if offer_id not in self.offers:
            raise KeitaroError(f"Keitaro GET /offers/{offer_id} → 404", status_code=404)
        return self.offers[offer_id]

    def list_campaigns(self, offset: int = 0, limit: int = 100):
        rows = list(self.campaigns.values())
        return rows[offset : offset + limit]

    def get_campaign(self, campaign_id):
        if campaign_id not in self.campaigns:
            raise KeitaroError(
                f"Keitaro GET /campaigns/{campaign_id} → 404: not found",
                status_code=404,
            )
        return copy.deepcopy(self.campaigns[campaign_id])

    def create_campaign(self, payload):
        self._maybe_fail("create_campaign")
        cid = self._id()
        self.campaigns[cid] = dict(payload, id=cid)
        return self.campaigns[cid]

    def delete_campaign(self, campaign_id):
        self._maybe_fail("delete_campaign")
        if campaign_id not in self.campaigns:
            raise KeitaroError(
                f"Keitaro DELETE /campaigns/{campaign_id} → 404: not found",
                status_code=404,
            )
        self.deleted_campaigns.append(campaign_id)
        del self.campaigns[campaign_id]
        for sid in [s for s, v in self.streams.items() if v["campaign_id"] == campaign_id]:
            del self.streams[sid]

    def create_stream(self, payload):
        if payload.get("schema") == "landings":
            self._maybe_fail("create_offers_stream")
        sid = self._id()
        self.streams[sid] = copy.deepcopy(dict(payload, id=sid))
        return copy.deepcopy(self.streams[sid])

    def list_campaign_streams(self, campaign_id):
        self._maybe_fail("list_campaign_streams")
        if campaign_id not in self.campaigns:
            raise KeitaroError(
                f"Keitaro GET /campaigns/{campaign_id}/streams → 404: not found",
                status_code=404,
            )
        return [
            {"id": s["id"], "name": s["name"], "type": s.get("type")}
            for s in self.streams.values()
            if s["campaign_id"] == campaign_id
        ]

    def get_stream(self, stream_id):
        self._maybe_fail("get_stream")
        if stream_id not in self.streams:
            raise KeitaroError(
                f"Keitaro GET /streams/{stream_id} → 404: Stream not found",
                status_code=404,
            )
        return copy.deepcopy(self.streams[stream_id])

    def update_stream(self, stream_id, payload):
        self._maybe_fail("update_stream")
        if stream_id not in self.streams:
            raise KeitaroError(
                f"Keitaro PUT /streams/{stream_id} → 404: Stream not found",
                status_code=404,
            )
        self.streams[stream_id].update(copy.deepcopy(payload))
        return copy.deepcopy(self.streams[stream_id])
