from fastapi import APIRouter, Query

from app.api.errors import keitaro_http_error
from app.schemas import OfferOut
from app.services.keitaro import KeitaroClient, KeitaroError

router = APIRouter(prefix="/api/offers", tags=["offers"])


@router.get("", response_model=list[OfferOut])
def search_offers(
    q: str = Query("", description="Поиск по имени или ID"),
    limit: int = Query(30, ge=1, le=500),
    refresh: bool = Query(False, description="Сбросить кэш каталога офферов"),
):
    try:
        with KeitaroClient() as client:
            if refresh:
                client.list_offers(force=True)
            rows = client.search_offers(q, limit=limit)
    except KeitaroError as exc:
        raise keitaro_http_error(exc) from exc

    return [
        OfferOut(
            id=int(row["id"]),
            name=str(row.get("name") or f"Offer {row['id']}"),
            group_id=row.get("group_id"),
            country=row.get("country"),
        )
        for row in rows
    ]
