from fastapi import HTTPException

from app.services.keitaro import KeitaroError
from app.services.sync import CampaignMissingInKeitaroError


def keitaro_http_error(exc: KeitaroError) -> HTTPException:
    """Ошибка внешнего API — всегда 502; исходный код Keitaro остаётся в тексте ошибки."""
    return HTTPException(status_code=502, detail=str(exc))


def campaign_missing_http_error(exc: CampaignMissingInKeitaroError) -> HTTPException:
    """Кампании больше нет в Keitaro — 410, UI предлагает удалить локальную копию."""
    return HTTPException(
        status_code=410,
        detail={
            "message": str(exc),
            "code": "campaign_missing_in_kt",
            "campaign_id": exc.campaign_id,
            "keitaro_campaign_id": exc.keitaro_campaign_id,
        },
    )
