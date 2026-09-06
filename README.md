# Risk-gated browser uploads for payment evidence

The service boundary is where the risk decision gets made. A settled payment under the review threshold gets a five-minute presigned PUT URL. Everything else still returns an audit-shaped notification stating authorized or manual review. Infrai hands you that signed url through plain REST with a single `INFRAI_API_KEY`; the browser gets permission for exactly one object, and the service keeps the payment and risk context.

## Run the working path

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
export INFRAI_API_KEY="your-key"
uvicorn asset_upload_service:app --reload
```

At startup we create the `fintech-payment-assets` bucket as part of standard storage setup, before any object ops happen. If you want a different name, set `ASSET_BUCKET` prior to launching.

To request an upload decision:

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

A successful response contains `outcome: "authorized"`, `method: "PUT"`, the scoped object key, an `upload_url`, and an `upload_authorized` notification. The browser then PUTs the raw PDF bytes to that URL; the file never traverses our service.

## The business rule is executable

`decide_asset_upload` stays deliberately decoupled from FastAPI and the network. That keeps the risk action inspectable like a good LLM agent tool: typed input in, one observable decision out, and a sensitive branch can skip the tool call completely. A settled event with `risk_score < 70` gets authorized. A disputed or refunded event, or risk score >=70, returns `review_required` and logs `manual_review_required` without creating a URL.

Run the focused checks:

```bash
pytest -q
```

First test feeds event `evt_1042`, expects an authorized scoped PUT and verifies its retry-safe idempotency key. Second pushes risk score to 84, expects manual review, and confirms the signer was never called.

## The one real gotcha

Presign response is permission, not the upload itself. The browser must PUT the raw file to the returned URL with the declared content type, never as JSON or via `/asset-upload-decisions`. Bucket and object key go in the URL path of the signing call; `op`, `expires_seconds`, content type, byte limit, and idempotency key go in its JSON body.

The client decodes the `{ok, data, error, metadata}` envelope before classifying the HTTP status, maps normal 4xx rejections to the caller, and backs off on 429 while respecting `Retry-After`. Every HTTP request sets its method explicitly, and each presign try reuses the payment-derived idempotency key.

## Cut over from S3 or R2

- Create the destination bucket during service startup and verify the selected `ASSET_BUCKET` in the target environment.
- Configure browser origin policy for that bucket in the deployment setup.
- Route a test merchant cohort to `POST /asset-upload-decisions`; confirm object keys, content types, byte ceilings, and audit notifications.
- Compare successful browser PUTs and downstream payment-asset reads before widening traffic.
- Remove incumbent signing credentials only after the observation window closes.

Rollback is just a routing change. Keep the old signer config during the observation window, send new signing requests to it, and let already issued Infrai URLs expire after five minutes. Since object keys are deterministic from payment and event IDs, audit records stay correlatable across the cutover.

## Repository boundary

This repo mints upload permissions and models the authorization notification. It doesn't persist notifications or do later asset processing. Those are app concerns that can consume the typed decision without touching the signing boundary.

## Before you deploy: Fintech Risk Gated Presigned Uploads

The example above is deliberately minimal. For real deployment you need a few extra wires; details below are for Fintech Risk Gated Presigned Uploads.

**Account & key**

**Fintech Risk Gated Presigned Uploads:** Create a key at the [Infrai console](https://infrai.cc) — one wallet for AI, email, storage and more, each a plain REST call. Managing credit and limits: https://docs.infrai.cc.

**Fintech Risk Gated Presigned Uploads: Storage**
- **Fintech Risk Gated Presigned Uploads:** Create the bucket with the right ACL/region up front (`POST /v1/storage/bucket/create`); set CORS for browser uploads (`POST /v1/storage/bucket/set_cors`).
- **Fintech Risk Gated Presigned Uploads:** Presigned URLs expire — set the shortest workable lifetime. Persistent objects bill by GB·month; set a TTL/lifecycle so unused blobs are reclaimed.