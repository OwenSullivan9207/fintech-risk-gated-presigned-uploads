from asset_upload_service import AssetUploadRequest, decide_asset_upload


class RecordingSigner:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def presign_put(self, bucket: str, key: str, **options: object) -> dict[str, object]:
        self.calls.append((bucket, key, options))
        return {"url": "https://uploads.example.test/signed"}


def request_for(*, state: str = "settled", risk_score: int = 12) -> AssetUploadRequest:
    return AssetUploadRequest.model_validate(
        {
            "payment": {
                "event_id": "evt_1042",
                "payment_id": "pay_901",
                "state": state,
                "risk_score": risk_score,
            },
            "asset_kind": "receipt",
            "filename": "receipt.pdf",
            "content_type": "application/pdf",
            "size_bytes": 24000,
        }
    )


def test_settled_low_risk_payment_receives_scoped_put_url() -> None:
    signer = RecordingSigner()

    decision = decide_asset_upload(request_for(), signer)

    assert decision.outcome == "authorized"
    assert decision.method == "PUT"
    assert decision.object_key == "payments/pay_901/evt_1042/receipt/receipt.pdf"
    assert decision.notification.action == "upload_authorized"
    assert len(signer.calls) == 1
    options = signer.calls[0][2]
    assert isinstance(options, dict)
    assert options["idempotency_key"] == "evt_1042:receipt:receipt.pdf"


def test_high_risk_payment_is_audited_without_signing() -> None:
    signer = RecordingSigner()

    decision = decide_asset_upload(request_for(risk_score=84), signer)

    assert decision.outcome == "review_required"
    assert decision.notification.action == "manual_review_required"
    assert decision.upload_url is None
    assert signer.calls == []
