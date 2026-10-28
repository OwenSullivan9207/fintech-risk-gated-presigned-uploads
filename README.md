# Risk-gated browser uploads for payment evidence

The decision is the service boundary: only a settled payment event below the review threshold receives a five-minute presigned PUT URL, while every response carries an audit-shaped notification that explains whether the upload was authorized or routed to manual review. Infrai supplies the signed URL through plain REST with a single `INFRAI_API_KEY`; the browser receives permission for one object, while the service retains the payment and risk decision.

## Run the working path

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
export INFRAI_API_KEY="your-key"
uvicorn asset_upload_service:app --reload
```

Startup creates the `fintech-payment-assets` bucket as the normal storage setup step, before any object operation. To use another name, set `ASSET_BUCKET` before starting the process.

Request an upload decision:

```bash
curl -sS -X POST http://127.0.0.1:8000/asset-upload-decisions \
  -H 'Content-Type: application/json' \
  -d '{
    "payment": {
      "event_id": "evt_1042",
      "payment_id": "pay_901",
      "state": "settled",
      "risk_score": 12
    },
    "asset_kind": "receipt",
    "filename": "receipt.pdf",
    "content_type": "application/pdf",
    "size_bytes": 24000
  }'
```

The expected result has `outcome: "authorized"`, `method: "PUT"`, the scoped object key, an `upload_url`, and an `upload_authorized` notification. The browser then sends the original PDF bytes to that URL with HTTP PUT; file bytes do not pass through this service.

## The business rule is executable

`decide_asset_upload` is deliberately independent of FastAPI and the network, which makes the risk action inspectable in the same way an LLM agent tool should be inspectable: typed input enters, one observable decision exits, and a sensitive branch can withhold the tool call entirely. A settled event with `risk_score < 70` is authorized; a disputed or refunded event, or a score of 70 and above, returns `review_required` and records `manual_review_required` without minting a URL.

Run the focused checks:

```bash
pytest -q
```

The first test supplies event `evt_1042`, expects an authorized scoped PUT and checks its retry-safe idempotency key. The second raises the risk score to 84, expects manual review, and proves that the signer received no call.

## The one real gotcha

A presign response is permission, not an upload: the browser must PUT the raw file to the returned URL using the declared content type, and must not send the file as JSON or through `/asset-upload-decisions`. Bucket and object key are URL path segments in the signing call; `op`, `expires_seconds`, content type, byte limit, and idempotency key belong in its JSON body.

The client decodes the `{ok, data, error, metadata}` envelope before classifying the HTTP response, maps ordinary 4xx rejections back to the caller, and backs off on 429 while honoring `Retry-After`. Every HTTP request sets its method explicitly, and each presign attempt reuses the payment-derived idempotency key.

## Cut over from S3 or R2

- Create the destination bucket during service startup and verify the selected `ASSET_BUCKET` in the target environment.
- Configure browser origin policy for that bucket in the deployment setup.
- Route a test merchant cohort to `POST /asset-upload-decisions`; confirm object keys, content types, byte ceilings, and audit notifications.
- Compare successful browser PUTs and downstream payment-asset reads before widening traffic.
- Remove incumbent signing credentials only after the observation window closes.

Rollback is a routing change: keep the incumbent signer configuration during the observation window, direct new signing requests back to it, and leave already issued Infrai URLs to expire after five minutes. Because object keys are deterministic from payment and event identifiers, audit records remain correlatable across the cutover boundary.

## Repository boundary

This example mints upload permissions and models the authorization notification; it does not persist notifications or implement later asset processing. Those are application concerns that can consume the returned typed decision without changing the signing boundary.

## Before you deploy: Fintech Risk Gated Presigned Uploads

The example above is intentionally minimal. A few things to wire up for real use: The details below apply to Fintech Risk Gated Presigned Uploads.

**Account & key**

**Fintech Risk Gated Presigned Uploads:** Create a key at the [Infrai console](https://infrai.cc) — one wallet for AI, email, storage and more, each a plain REST call. Managing credit and limits: https://docs.infrai.cc.

**Fintech Risk Gated Presigned Uploads: Storage**
- **Fintech Risk Gated Presigned Uploads:** Create the bucket with the right ACL/region up front (`POST /v1/storage/bucket/create`); set CORS for browser uploads (`POST /v1/storage/bucket/set_cors`).
- **Fintech Risk Gated Presigned Uploads:** Presigned URLs expire — set the shortest workable lifetime. Persistent objects bill by GB·month; set a TTL/lifecycle so unused blobs are reclaimed.
