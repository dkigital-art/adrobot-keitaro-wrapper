"""Общие фикстуры pytest: SQLite in-memory + FakeKeitaro без сети."""

from __future__ import annotations

from unittest import mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.db import Base
from app.models import SyncLog
from app.services.campaigns import create_campaign_with_streams
from app.services.sync import (
    add_offer_to_stream,
    get_stream_or_404,
    push_stream_to_keitaro,
)
from tests.fakes import FakeKeitaro


@pytest.fixture(autouse=True)
def _no_keitaro_warmup(monkeypatch):
    """TestClient поднимает lifespan — не ходим в реальный Keitaro из фонового warm-up."""
    monkeypatch.setattr("app.main.warm_up_caches", lambda: None)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def kt():
    return FakeKeitaro()


@pytest.fixture
def api_client(kt):
    """HTTP-клиент приложения поверх отдельной in-memory БД и FakeKeitaro."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    try:
        with (
            mock.patch("app.api.campaigns.KeitaroClient", return_value=kt),
            mock.patch("app.api.streams.KeitaroClient", return_value=kt),
            TestClient(app) as client,
        ):
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


@pytest.fixture
def campaign(db, kt):
    return create_campaign_with_streams(db, name="Test", geo="AU", offer_id=1, client=kt)


@pytest.fixture
def offers_stream(db, campaign):
    stream_id = next(s.id for s in campaign.streams if s.has_offers)
    return get_stream_or_404(db, stream_id)


@pytest.fixture
def published_two(db, kt, campaign, offers_stream):
    stream = add_offer_to_stream(db, offers_stream, 2, kt)
    stream = push_stream_to_keitaro(db, stream, kt)
    return campaign, stream


def offers_state(stream):
    return sorted(
        (o.keitaro_offer_id, o.share, o.pinned, o.status) for o in stream.offers
    )


def sync_logs(db, campaign_id, action=None):
    q = db.query(SyncLog).filter(SyncLog.campaign_id == campaign_id)
    if action:
        q = q.filter(SyncLog.action == action)
    return q.order_by(SyncLog.id).all()
