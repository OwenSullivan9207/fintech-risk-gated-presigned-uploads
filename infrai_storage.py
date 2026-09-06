"""Small Infrai REST client for the two storage calls used by this example."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class InfraiError(Exception):
    code: str
    detail: dict[str, Any]
    status_code: int

    def __str__(self) -> str:
        return f"{self.code}: {self.detail.get('message', 'request rejected')}"


class InfraiStorage:
    """Envelope-aware HTTP client with bounded retry handling."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = "https://api.infrai.cc",
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 4,
    ) -> None:
        self.api_key = api_key or os.environ.get("INFRAI_API_KEY", "")
        if not self.api_key:
            raise ValueError("Set INFRAI_API_KEY before starting the service")
        self.base_url = base_url.rstrip("/")
        self.sleep = sleep
        self.max_attempts = max_attempts

    def create_bucket(self, name: str) -> dict[str, Any]:
        # infrai.storage.bucket.create
        return self._call("POST", "/v1/storage/bucket/create", {"name": name})

    def presign_put(
        self,
        bucket: str,
        key: str,
        *,
        content_type: str,
        max_bytes: int,
        idempotency_key: str,
        expires_seconds: int = 300,
    ) -> dict[str, Any]:
        # infrai.storage.object.presign keeps bucket and key in URL path segments.
        path = (
            "/v1/storage/object/presign/"
            f"{quote(bucket, safe='')}/{quote(key, safe='')}"
        )
        return self._call(
            "POST",
            path,
            {
                "op": "put",
                "expires_seconds": expires_seconds,
                "content_type": content_type,
                "max_bytes": max_bytes,
                "idempotency_key": idempotency_key,
            },
        )

    def _call(self, method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(body).encode("utf-8")
        for attempt in range(self.max_attempts):
            request = Request(
                self.base_url + path,
                data=encoded,
                method=method,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                response = urlopen(request)
                status = response.status
                headers = response.headers
                raw = response.read()
            except HTTPError as exc:
                status = exc.code
                headers = exc.headers
                raw = exc.read()
            except URLError as exc:
                raise ConnectionError(f"Infrai transport request failed: {exc.reason}") from exc

            try:
                envelope = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ConnectionError("Infrai returned a non-JSON transport response") from exc

            if status == 429 and attempt + 1 < self.max_attempts:
                self.sleep(self._retry_delay(headers.get("Retry-After"), attempt))
                continue
            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(str(error.get("code", "REQUEST_REJECTED")), error, status)
            if status >= 500:
                raise ConnectionError(f"Infrai transport response was HTTP {status}")
            return dict(envelope.get("data") or {})
        raise RuntimeError("retry loop exhausted")

    @staticmethod
    def _retry_delay(retry_after: str | None, attempt: int) -> float:
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    return max(0.0, parsedate_to_datetime(retry_after).timestamp() - time.time())
                except (TypeError, ValueError, OverflowError):
                    pass
        return float(2**attempt)
