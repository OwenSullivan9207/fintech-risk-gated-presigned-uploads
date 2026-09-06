"""Runnable fintech asset upload service and its risk decision."""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import Annotated, Literal, Protocol

try:
    from fastapi import Depends, FastAPI, HTTPException
    from pydantic import BaseModel, Field
except ModuleNotFoundError:  # Keep pure decision logic usable in minimal test environments.
    class BaseModel:
        def __init__(self, **values: object) -> None:
            for name, value in values.items():
                setattr(self, name, value)

        @classmethod
        def model_validate(cls, values: dict[str, object]):
            from typing import get_type_hints
            try:
                annotations = get_type_hints(cls, vars(sys.modules[cls.__module__]))
            except (NameError, TypeError):
                annotations = getattr(cls, "__annotations__", {})
            normalized = dict(values)
            for name, annotation in annotations.items():
                value = normalized.get(name)
                if isinstance(value, dict) and isinstance(annotation, type) and issubclass(annotation, BaseModel):
                    normalized[name] = annotation.model_validate(value)
            return cls(**normalized)

    def Field(default: object = ..., **_: object) -> object:
        return default

    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: object = None) -> None:
            self.status_code, self.detail = status_code, detail

    class Depends:
        def __init__(self, dependency: object) -> None:
            self.dependency = dependency

    class FastAPI:
        def __init__(self, **_: object) -> None: pass
        def post(self, *_: object, **__: object):
            return lambda function: function

from infrai_storage import InfraiError, InfraiStorage

ASSET_BUCKET = os.environ.get("ASSET_BUCKET", "fintech-payment-assets")
MAX_ASSET_BYTES = 8 * 1024 * 1024


class PaymentEvent(BaseModel):
    event_id: str = Field(pattern=r"^evt_[A-Za-z0-9_-]+$")
    payment_id: str = Field(pattern=r"^pay_[A-Za-z0-9_-]+$")
    state: Literal["settled", "disputed", "refunded"]
    risk_score: int = Field(ge=0, le=100)


class AssetUploadRequest(BaseModel):
    payment: PaymentEvent
    asset_kind: Literal["receipt", "identity_evidence"]
    filename: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9._-]+$")
    content_type: Literal["application/pdf", "image/jpeg", "image/png"]
    size_bytes: int = Field(gt=0, le=MAX_ASSET_BYTES)


class AuditNotification(BaseModel):
    event_id: str
    action: Literal["upload_authorized", "manual_review_required"]
    reason: str


class UploadDecision(BaseModel):
    outcome: Literal["authorized", "review_required"]
    notification: AuditNotification
    object_key: str | None = None
    upload_url: str | None = None
    method: Literal["PUT"] | None = None
    expires_seconds: int | None = None


class StorageSigner(Protocol):
    def presign_put(
        self,
        bucket: str,
        key: str,
        *,
        content_type: str,
        max_bytes: int,
        idempotency_key: str,
        expires_seconds: int = 300,
    ) -> dict[str, object]:
        """Return signing data for one PUT operation."""


def decide_asset_upload(request: AssetUploadRequest, signer: StorageSigner) -> UploadDecision:
    payment = request.payment
    if payment.state != "settled" or payment.risk_score >= 70:
        reason = "risk review required" if payment.risk_score >= 70 else "payment is not settled"
        return UploadDecision(
            outcome="review_required",
            notification=AuditNotification(
                event_id=payment.event_id,
                action="manual_review_required",
                reason=reason,
            ),
        )

    object_key = f"payments/{payment.payment_id}/{payment.event_id}/{request.asset_kind}/{request.filename}"
    expires_seconds = 300
    signed = signer.presign_put(
        ASSET_BUCKET,
        object_key,
        content_type=request.content_type,
        max_bytes=request.size_bytes,
        idempotency_key=f"{payment.event_id}:{request.asset_kind}:{request.filename}",
        expires_seconds=expires_seconds,
    )
    return UploadDecision(
        outcome="authorized",
        notification=AuditNotification(
            event_id=payment.event_id,
            action="upload_authorized",
            reason="settled payment passed the risk threshold",
        ),
        object_key=object_key,
        upload_url=str(signed["url"]),
        method="PUT",
        expires_seconds=expires_seconds,
    )


storage: InfraiStorage | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global storage
    storage = InfraiStorage()
    yield
    storage = None


app = FastAPI(title="Fintech asset upload authorization", lifespan=lifespan)


def get_storage() -> InfraiStorage:
    if storage is None:
        raise RuntimeError("storage client is not initialized")
    return storage


@app.post("/asset-upload-decisions", response_model=UploadDecision)
def authorize_asset_upload(
    request: AssetUploadRequest,
    signer: Annotated[InfraiStorage, Depends(get_storage)],
) -> UploadDecision:
    try:
        return decide_asset_upload(request, signer)
    except InfraiError as exc:
        client_status = exc.status_code if 400 <= exc.status_code < 500 else 502
        raise HTTPException(
            status_code=client_status,
            detail={"code": exc.code, "message": str(exc.detail.get("message", "request rejected"))},
        ) from exc
    except (ConnectionError, KeyError) as exc:
        raise HTTPException(status_code=502, detail="storage signing request could not be completed") from exc
