"""Jitter between Google requests, and exponential backoff when throttled."""

from __future__ import annotations

import random
import time
from collections.abc import Callable

THROTTLE_ATTEMPTS = 5
THROTTLE_BASE_SECONDS = 2.0
THROTTLE_CAP_SECONDS = 60.0


def jitter_pause(low: float, high: float, sleep: Callable[[float], None] | None = None) -> None:
    """Sleep a random duration inside the configured delay window."""
    pause = sleep or time.sleep
    if high <= 0:
        return
    pause(random.uniform(low, high))


def is_throttle(exc: BaseException) -> bool:
    """True when Google, fli, or fast-flights is asking the caller to slow down."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(current, "status_code", None)
        if status == 429:
            return True
        text = str(current).lower()
        if any(token in text for token in ("429", "too many requests", "throttl", "rate limit", "rate-limited")):
            return True
        current = current.__cause__ or current.__context__
    return False


def throttle_delay(attempt: int, rng: Callable[[], float] | None = None) -> float:
    """Exponential delay with a little jitter. ``attempt`` starts at 0."""
    roll = random.random if rng is None else rng
    base = min(THROTTLE_CAP_SECONDS, THROTTLE_BASE_SECONDS * (2 ** attempt))
    return base + roll() * base * 0.25


def call_with_backoff(
    fn: Callable[[], object],
    *,
    attempts: int = THROTTLE_ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
    delay: Callable[[int], float] | None = None,
):
    """Call ``fn``. On a throttle error, wait and try again. Other errors raise immediately."""
    pause = sleep or time.sleep
    wait_for = delay or throttle_delay
    last: BaseException | None = None
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except Exception as exc:
            last = exc
            if not is_throttle(exc) or attempt >= attempts - 1:
                raise
            seconds = wait_for(attempt)
            print(f"throttled, backing off {seconds:.1f}s (attempt {attempt + 1}/{attempts})", flush=True)
            pause(seconds)
    if last is not None:
        raise last
    raise RuntimeError("backoff ended without a result")
