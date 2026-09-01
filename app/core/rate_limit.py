"""Small, dependency-free throttling for public authentication endpoints."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from ipaddress import ip_address
import logging
from math import ceil
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request, status

from app.core.config import settings


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after: int = 0


class InMemoryRateLimiter:
    """Thread-safe sliding-window limiter scoped to one application process."""

    def __init__(self) -> None:
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._checks = 0

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        now = monotonic()
        cutoff = now - window_seconds

        with self._lock:
            self._checks += 1
            if self._checks % 1000 == 0:
                stale_keys = [
                    existing_key
                    for existing_key, existing_timestamps in self._requests.items()
                    if not existing_timestamps or existing_timestamps[-1] <= cutoff
                ]
                for stale_key in stale_keys:
                    del self._requests[stale_key]

            timestamps = self._requests[key]
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) >= limit:
                retry_after = max(1, ceil(timestamps[0] + window_seconds - now))
                return RateLimitDecision(False, retry_after)

            timestamps.append(now)
            return RateLimitDecision(True)

    def reset(self) -> None:
        """Clear process-local state (primarily useful for isolated tests)."""
        with self._lock:
            self._requests.clear()
            self._checks = 0


auth_rate_limiter = InMemoryRateLimiter()
public_form_rate_limiter = InMemoryRateLimiter()


def _normalized_ip(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value.strip()))
    except ValueError:
        return None


def get_client_address(request: Request) -> str:
    """Resolve a client address without trusting user-supplied leftmost hops.

    When trusted proxy hops are configured, the selected address is counted
    from the right of X-Forwarded-For. This prevents a caller from changing an
    arbitrary leftmost value to evade the limiter.
    """
    trusted_hops = settings.auth_rate_limit_trusted_proxy_hops
    if trusted_hops:
        forwarded = request.headers.get("x-forwarded-for", "")
        addresses = [part.strip() for part in forwarded.split(",") if part.strip()]
        if len(addresses) >= trusted_hops:
            candidate = _normalized_ip(addresses[-trusted_hops])
            if candidate is not None:
                return candidate

    direct_address = request.client.host if request.client else None
    return _normalized_ip(direct_address) or "unknown"


def _enforce(request: Request, *, bucket: str, limit: int) -> None:
    if not settings.auth_rate_limit_enabled:
        return

    decision = auth_rate_limiter.check(
        f"{bucket}:{get_client_address(request)}",
        limit=limit,
        window_seconds=settings.auth_rate_limit_window_seconds,
    )
    if not decision.allowed:
        logger.warning(
            "Authentication request throttled: endpoint=%s retry_after=%s",
            bucket,
            decision.retry_after,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(decision.retry_after)},
        )


def enforce_login_rate_limit(request: Request) -> None:
    _enforce(request, bucket="login", limit=settings.login_rate_limit_requests)


def enforce_registration_rate_limit(request: Request) -> None:
    _enforce(
        request,
        bucket="registration",
        limit=settings.registration_rate_limit_requests,
    )


def enforce_public_form_rate_limit(request: Request, *, submission: bool) -> None:
    if not settings.auth_rate_limit_enabled:
        return
    decision = public_form_rate_limiter.check(
        f"public-form-{'post' if submission else 'get'}:{get_client_address(request)}",
        limit=(settings.public_form_submit_rate_limit_requests if submission else settings.public_form_get_rate_limit_requests),
        window_seconds=settings.auth_rate_limit_window_seconds,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please try again later.",
            headers={"Retry-After": str(decision.retry_after)},
        )
