"""
Rate limits and the daily LLM budget (plan M4, section 3's call budget).

Per request, before any model call (Limiter.check_request):
    per device    config.RATE_DEVICE_PER_MINUTE a minute, config.RATE_DEVICE_PER_DAY a day
    per address   config.RATE_ADDRESS_PER_MINUTE a minute
A request without a device ID counts against a device bucket made from its
address. Every request counts, including refused ones, so a script that
keeps hammering stays refused.

Per model call (Limiter.spend_llm_call), so cache hits cost nothing:
    global        config.LLM_DAILY_BUDGET calls a UTC day (the Groq free tier resets then)
    per app key   config.KEY_DAILY_LLM_CAPS, optional, so one tester group can't
                  use up the pitch devices' share

Windows are fixed: the current UTC minute or day. Counters live in the store
(Supabase in production, shared by every instance). If the store fails, an
in-memory counter takes over for this process, so a database outage never
switches the limits off.

Device IDs and addresses are counted hashed, never stored raw.
"""
import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from app import config
from app.llm_client import LLMUnavailableError
from app.store import Store, StoreSkipped
from app.utils import hash_address, hash_device_id

log = logging.getLogger(__name__)

MINUTE, DAY = 60, 86400
TOO_FAST, DAILY_LIMIT, SERVICE_LIMIT = "too_fast", "daily_limit", "service_limit"


class RateLimited(Exception):
    def __init__(self, reason: str, retry_after_s: int):
        super().__init__(reason)
        self.reason, self.retry_after_s = reason, retry_after_s


class LLMBudgetExceeded(LLMUnavailableError):
    """The day's model budget is used up. A subclass of LLMUnavailableError, so
    code that already fails safely on an LLM outage fails safely here too."""

    def __init__(self, retry_after_s: int):
        super().__init__("daily LLM budget reached")
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class _Count:
    bucket: str
    window_s: int
    limit: int
    reason: str


def _window(now: float, window_s: int) -> tuple[int, int]:
    """(window start, seconds until the next window) for a fixed window."""
    start = int(now // window_s) * window_s
    return start, max(1, start + window_s - int(now))


class MemoryCounters:
    """Per-process fallback with the store's incr_counters shape."""

    def __init__(self):
        self._counts: dict[tuple[str, int], tuple[int, int]] = {}   # (bucket, start) -> (count, window_s)
        self._lock = threading.Lock()

    def incr_counters(self, items: list[tuple[str, int, int]]) -> list[int]:
        now = time.time()
        with self._lock:
            if len(self._counts) > 10_000:
                self._counts = {k: v for k, v in self._counts.items() if k[1] + v[1] > now}
            out = []
            for bucket, start, window_s in items:
                count = self._counts.get((bucket, start), (0, window_s))[0] + 1
                self._counts[(bucket, start)] = (count, window_s)
                out.append(count)
            return out


def parse_pairs(value: str) -> dict[str, str]:
    """'a=1, b=2' -> {'a': '1', 'b': '2'}. Blank entries are skipped."""
    pairs = {}
    for part in value.split(","):
        name, sep, val = part.strip().partition("=")
        if sep and name.strip() and val.strip():
            pairs[name.strip()] = val.strip()
    return pairs


class Limiter:
    def __init__(self, store: Store, clock: Callable[[], float] = time.time):
        self.store = store
        self.memory = MemoryCounters()
        self.clock = clock
        self._warned_budget_day: Optional[int] = None

    def _incr(self, counts: list[_Count], now: float) -> list[int]:
        # The window length is part of the bucket: at 00:00 UTC a minute and a day start together.
        items = [(f"{c.bucket}/{c.window_s}", _window(now, c.window_s)[0], c.window_s) for c in counts]
        try:
            return self.store.incr_counters(items)
        except StoreSkipped:
            pass   # the database is down; the circuit breaker already logged it
        except Exception as e:  # fail open to the in-memory counters, never to "no limits"
            log.warning("rate counter error", extra={"fields": {"error_class": type(e).__name__}})
        return self.memory.incr_counters(items)

    def _check(self, counts: list[_Count]) -> None:
        now = self.clock()
        over = [(c, _window(now, c.window_s)[1]) for c, n in zip(counts, self._incr(counts, now), strict=True)
                if n > c.limit]
        if over:
            # The longest wait wins: a device over its daily limit isn't told "wait a minute".
            c, retry = max(over, key=lambda x: x[1])
            log.info("rate limited", extra={"fields": {"limit": c.bucket.split(":")[0] + f"/{c.window_s}s",
                                                       "reason": c.reason}})
            raise RateLimited(c.reason, retry)

    def check_request(self, device_id: Optional[str], address: Optional[str]) -> None:
        addr = hash_address(address)
        device = hash_device_id(device_id) or f"a{addr}"
        self._check([
            _Count(f"device:{device}", MINUTE, config.RATE_DEVICE_PER_MINUTE, TOO_FAST),
            _Count(f"device:{device}", DAY, config.RATE_DEVICE_PER_DAY, DAILY_LIMIT),
            _Count(f"addr:{addr}", MINUTE, config.RATE_ADDRESS_PER_MINUTE, TOO_FAST),
        ])

    def spend_llm_call(self, key_name: Optional[str] = None) -> None:
        """Count one model call; raises LLMBudgetExceeded if it's over a daily budget."""
        counts = [_Count("llm:global", DAY, config.LLM_DAILY_BUDGET, SERVICE_LIMIT)]
        key_cap = parse_pairs(config.KEY_DAILY_LLM_CAPS).get(key_name or "")
        if key_cap and key_cap.isdigit():
            counts.append(_Count(f"llm:key:{key_name}", DAY, int(key_cap), SERVICE_LIMIT))
        now = self.clock()
        used = self._incr(counts, now)
        self._warn_at_80(used[0], now)
        if any(n > c.limit for c, n in zip(counts, used, strict=True)):
            log.warning("llm budget reached", extra={"fields": {"key": key_name, "global_used": used[0]}})
            raise LLMBudgetExceeded(_window(now, DAY)[1])

    def _warn_at_80(self, used: int, now: float) -> None:
        day = _window(now, DAY)[0]
        if used >= 0.8 * config.LLM_DAILY_BUDGET and self._warned_budget_day != day:
            self._warned_budget_day = day
            log.warning("llm budget at 80%", extra={"fields": {"used": used, "budget": config.LLM_DAILY_BUDGET}})

    def budget_for(self, key_name: Optional[str]) -> Callable[[], None]:
        """A callable the pipeline runs before each model call."""
        return lambda: self.spend_llm_call(key_name)
