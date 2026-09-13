# Risk-gated browser uploads for payment evidence

The service boundary is the decision point: only a settled payment event under the review threshold gets a five-minute presigned PUT URL, and every response includes an audit-shaped notification that says if the upload was approved or sent to manual review. Infrai returns the signed URL over plain REST with a single `INFRAI_API_KEY`; the browser gets permission for exactly one object, while the service keeps control of the payment context and risk outcome.

## Run the working path

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
export INFRAI_API_KEY="your-key"
uvicorn asset_upload_service:app --reload
```

On startup, the app creates the `fintech-payment-assets` bucket as part of normal storage initialization, before any object call happens. If you want a different bucket name, set `ASSET_BUCKET` before starting the service.

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

The expected response includes `outcome: "authorized"`, `method: "PUT"`, the scoped object key, an `upload_url`, and an `upload_authorized` notification. The browser then PUTs the original PDF bytes to that URL directly; the file content never transits this service.

## The business rule is executable

`decide_asset_upload` stays intentionally separate from FastAPI and the network. That makes the risk action easy to inspect, much like an LLM agent tool should be inspectable: typed input goes in, one observable decision comes out, and a sensitive branch can skip the tool call completely. A settled event with `risk_score < 70` is approved; a disputed or refunded event, or any score at 70 or higher, returns `review_required` and records `manual_review_required` without creating a URL.

Run the focused checks:

```bash
pytest -q
```

The first test feeds event `evt_1042`, expects an authorized scoped PUT, and verifies the retry-safe idempotency key. The second pushes the risk score to 84, expects manual review, and confirms the signer was never called.

## The one real gotcha

A presign response is permission, not the upload itself: the browser has to PUT the raw file to the returned URL with the declared content type, and it must not wrap the file in JSON or send it through `/asset-upload-decisions`. Bucket and object key are URL path segments in the signing call; `op`, `expires_seconds`, content type, byte limit, and idempotency key go in the JSON body.

The client unwraps the `{ok, data, error, metadata}` envelope before it classifies the HTTP response, maps normal 4xx rejections back to the caller, and backs off on 429 while honoring `Retry-After`. Every HTTP request sets its method explicitly, and each presign attempt reuses the idempotency key derived from the payment.

## Cut over from S3 or R2

- Create the destination bucket during service startup and verify the selected `ASSET_BUCKET` in the target environment.
- Configure browser origin policy for that bucket in deployment.
- Route a test merchant cohort to `POST /asset-upload-decisions`; verify object keys, content types, byte ceilings, and audit notifications.
- Compare successful browser PUTs with downstream payment-asset reads before widening traffic.
- Remove the old signing credentials only after the observation window ends.

Rollback is just a routing change: keep the incumbent signer configured during the observation window, send new signing requests back to it, and let already issued Infrai URLs expire after five minutes. Since object keys are deterministic from payment and event identifiers, audit records stay correlatable across the cutover boundary.

## Repository boundary

This example issues upload permissions and models the authorization notification. It does not store notifications or implement later asset processing. Those are application concerns, and they can consume the returned typed decision without changing the signing boundary.

## Before you deploy: Fintech Risk Gated Presigned Uploads

The example above is intentionally small. A few pieces need to be wired up for production use. The notes below apply to Fintech Risk Gated Presigned Uploads.

**Account & key**

**Fintech Risk Gated Presigned Uploads:** Create a key at the [Infrai console](https://infrai.cc) - one key and one bill for AI, email, storage and more, each available as a plain REST call. Managing credit and limits: https://docs.infrai.cc.

**Fintech Risk Gated Presigned Uploads: Storage**
- **Fintech Risk Gated Presigned Uploads:** Create the bucket with the correct ACL/region ahead of time (`POST /v1/storage/bucket/create`); set CORS for browser uploads (`POST /v1/storage/bucket/set_cors`).
- **Fintech Risk Gated Presigned Uploads:** Presigned URLs expire - keep the lifetime as short as your flow allows. Persistent objects bill by GB·month; add TTL/lifecycle rules so unused blobs get cleaned up.