from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.errors import campaign_missing_http_error, keitaro_http_error
from app.db import get_db
from app.schemas import OfferAddRequest, OfferPatchRequest, StreamOut, serialize_stream
from app.services.keitaro import KeitaroClient, KeitaroError
from app.services.sync import (
    CampaignMissingInKeitaroError,
    add_offer_to_stream,
    bring_back_offer,
    cancel_stream_draft,
    get_stream_or_404,
    patch_offer,
    push_stream_to_keitaro,
    remove_offer,
)
from app.services.weights import WeightError

router = APIRouter(prefix="/api/streams", tags=["streams"])


@router.get("/{stream_id}", response_model=StreamOut)
def get_stream(stream_id: int, db: Session = Depends(get_db)):
    try:
        return serialize_stream(get_stream_or_404(db, stream_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{stream_id}/offers", response_model=StreamOut)
def add_offer(stream_id: int, body: OfferAddRequest, db: Session = Depends(get_db)):
    try:
        stream = get_stream_or_404(db, stream_id)
        with KeitaroClient() as client:
            updated = add_offer_to_stream(db, stream, body.offer_id, client)
        return serialize_stream(updated)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, WeightError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeitaroError as exc:
        raise keitaro_http_error(exc) from exc


@router.patch("/{stream_id}/offers/{offer_row_id}", response_model=StreamOut)
def update_offer(
    stream_id: int,
    offer_row_id: int,
    body: OfferPatchRequest,
    db: Session = Depends(get_db),
):
    try:
        stream = get_stream_or_404(db, stream_id)
        updated = patch_offer(
            db,
            stream,
            offer_row_id,
            pinned=body.pinned,
            share=body.share,
        )
        return serialize_stream(updated)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, WeightError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{stream_id}/offers/{offer_row_id}/remove", response_model=StreamOut)
def remove(stream_id: int, offer_row_id: int, db: Session = Depends(get_db)):
    try:
        stream = get_stream_or_404(db, stream_id)
        return serialize_stream(remove_offer(db, stream, offer_row_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, WeightError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{stream_id}/offers/{offer_row_id}/bring-back", response_model=StreamOut)
def bring_back(stream_id: int, offer_row_id: int, db: Session = Depends(get_db)):
    try:
        stream = get_stream_or_404(db, stream_id)
        return serialize_stream(bring_back_offer(db, stream, offer_row_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, WeightError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{stream_id}/push", response_model=StreamOut)
def push(stream_id: int, db: Session = Depends(get_db)):
    try:
        stream = get_stream_or_404(db, stream_id)
        with KeitaroClient() as client:
            updated = push_stream_to_keitaro(db, stream, client)
        return serialize_stream(updated)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WeightError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except CampaignMissingInKeitaroError as exc:
        raise campaign_missing_http_error(exc) from exc
    except KeitaroError as exc:
        raise keitaro_http_error(exc) from exc


@router.post("/{stream_id}/cancel", response_model=StreamOut)
def cancel(stream_id: int, db: Session = Depends(get_db)):
    try:
        stream = get_stream_or_404(db, stream_id)
        return serialize_stream(cancel_stream_draft(db, stream))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
