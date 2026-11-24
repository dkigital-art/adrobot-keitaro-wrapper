from collections.abc import Generator

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import PROJECT_ROOT, get_settings


settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def upgrade_database() -> None:
    """Приводит схему БД к последней миграции Alembic (alembic upgrade head)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    cfg.attributes["skip_logging_config"] = True

    tables = set(inspect(engine).get_table_names())
    if "campaigns" in tables and "alembic_version" not in tables:
        # БД создана старым create_all без Alembic: базовая схема уже есть
        command.stamp(cfg, "001_initial")
    command.upgrade(cfg, "head")
