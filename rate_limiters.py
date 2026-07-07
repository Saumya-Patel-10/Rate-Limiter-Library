"""
Four rate-limiting algorithms behind one common interface.
 
Common interface
----------------
Every limiter implements:  allow(client_id, timestamp) -> bool
 
  - `timestamp` is a float in SECONDS (monotonic-ish; the caller supplies it,
    so the algorithms never read the wall clock themselves -> fully testable).
  - Returns True if the request is ALLOWED, False if it should be REJECTED.
 
Per-client state lives in a hash map (dict) client_id -> state.  No unbounded
history is ever kept except, deliberately, in the Sliding Window *Log*, whose
whole point is to show the memory cost of being exact.
 
All limiters expose:
  - name           : short label used in tables/charts
  - state_bytes()  : approximate resident state size, for the memory comparison
  - peak_state()   : the single largest per-client state seen (worst case)
"""
 
from __future__ import annotations
 
import math
import sys
from abc import ABC, abstractmethod
from collections import deque
 
 
class RateLimiter(ABC):
    """Common interface. Swap any subclass in and the harness treats it identically."""
 
    name: str = "base"
 
    def __init__(self, limit: int, window: float):
        self.limit = limit          # max allowed requests ...
        self.window = float(window)  # ... per this many seconds
        self.store: dict = {}        # client_id -> per-client state (the core hash map)
 
    @abstractmethod
    def allow(self, client_id, timestamp: float) -> bool:
        ...
 
    # ---- introspection used only for the comparison, not the algorithm itself ----
    def state_bytes(self) -> int:
        """Rough total bytes of resident state across all clients."""
        return sum(_deep_size(v) for v in self.store.values())
 
    def peak_state(self) -> int:
        """Bytes held by the single heaviest client (worst-case footprint)."""
        return max((_deep_size(v) for v in self.store.values()), default=0)
 
 
# --------------------------------------------------------------------------- #
# 1. Fixed Window Counter
# --------------------------------------------------------------------------- #
class FixedWindowCounter(RateLimiter):
    """
    Bucket time into fixed windows [0,W), [W,2W), ...  Count per client per
    window; reset when the window rolls over.
 
    State: (window_index, count)   -> O(1) per client.
    Flaw : up to 2*limit can slip through across a boundary, because a full
           burst at the end of one window and another full burst at the start
           of the next are counted in *different* buckets.
    """
 
    name = "Fixed Window"
 
    def allow(self, client_id, timestamp: float) -> bool:
        widx = int(timestamp // self.window)
        cur = self.store.get(client_id)
        if cur is None or cur[0] != widx:
            cur = [widx, 0]           # new window -> counter resets
            self.store[client_id] = cur
        if cur[1] < self.limit:
            cur[1] += 1
            return True
        return False
 
 
# --------------------------------------------------------------------------- #
# 2. Sliding Window Log
# --------------------------------------------------------------------------- #
class SlidingWindowLog(RateLimiter):
    """
    Keep every timestamp per client in a deque.  On each request, evict from the
    front everything older than (t - W), then allow iff fewer than `limit`
    timestamps remain.  This is the EXACT sliding window -- it's the ground truth
    the other approximations are judged against.
 
    State: one float per in-window request  -> O(requests-in-window) per client.
    That unbounded-ish growth is the whole lesson here.
    """
 
    name = "Sliding Log"
 
    def allow(self, client_id, timestamp: float) -> bool:
        dq = self.store.get(client_id)
        if dq is None:
            dq = deque()
            self.store[client_id] = dq
        boundary = timestamp - self.window
        # two-pointer eviction: pop expired timestamps off the front
        while dq and dq[0] <= boundary:
            dq.popleft()
        if len(dq) < self.limit:
            dq.append(timestamp)
            return True
        return False
 
 
# --------------------------------------------------------------------------- #
# 3. Sliding Window Counter (the smart approximation)
# --------------------------------------------------------------------------- #
class SlidingWindowCounter(RateLimiter):
    """
    Approximate the sliding window with just TWO counters per client: the count
    in the current fixed window and the count in the previous one.  Estimate the
    rolling count by weighting the previous window by how much of it still
    overlaps the trailing W-second window:
 
        elapsed  = t - current_window_start          (0 .. W)
        weight   = (W - elapsed) / W                  (1 .. 0)
        estimate = current_count + previous_count * weight
 
    Allow iff estimate < limit.  O(1) memory, and far tighter at boundaries than
    the fixed window -- this is the one worth explaining line-by-line in an
    interview.
    """
 
    name = "Sliding Counter"
 
    def allow(self, client_id, timestamp: float) -> bool:
        widx = int(timestamp // self.window)
        st = self.store.get(client_id)
        if st is None:
            st = {"widx": widx, "cur": 0, "prev": 0}
            self.store[client_id] = st
        elif st["widx"] != widx:
            # roll the window forward. If we skipped >1 window, the previous
            # window is entirely stale -> its contribution is zero.
            if widx == st["widx"] + 1:
                st["prev"] = st["cur"]
            else:
                st["prev"] = 0
            st["cur"] = 0
            st["widx"] = widx
 
        elapsed = timestamp - widx * self.window
        weight = (self.window - elapsed) / self.window
        estimate = st["cur"] + st["prev"] * weight
        if estimate < self.limit:
            st["cur"] += 1
            return True
        return False
 
 
# --------------------------------------------------------------------------- #
# 4. Token Bucket
# --------------------------------------------------------------------------- #
class TokenBucket(RateLimiter):
    """
    Each client owns a bucket of `capacity` tokens that refills at `rate`
    tokens/sec.  A request costs one token; if the bucket has >= 1 token it's
    allowed and a token is spent, otherwise it's rejected.
 
    Configured so the *sustained* rate matches limit/window, but a client that
    has been quiet can spend up to `capacity` tokens at once -> controlled
    bursts, which the window approaches can't express.
 
    State: (tokens, last_refill_time)  -> O(1) per client.
    """
 
    name = "Token Bucket"
 
    def __init__(self, limit: int, window: float, capacity: int | None = None):
        super().__init__(limit, window)
        self.rate = limit / float(window)          # sustained tokens per second
        self.capacity = float(capacity if capacity is not None else limit)
 
    def allow(self, client_id, timestamp: float) -> bool:
        st = self.store.get(client_id)
        if st is None:
            st = [self.capacity, timestamp]        # [tokens, last_refill]
            self.store[client_id] = st
        tokens, last = st
        # refill for the elapsed time, capped at capacity
        tokens = min(self.capacity, tokens + (timestamp - last) * self.rate)
        st[1] = timestamp
        if tokens >= 1.0:
            st[0] = tokens - 1.0
            return True
        st[0] = tokens
        return False
 
 
# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _deep_size(obj) -> int:
    """Approximate bytes held by a small state object (dict/list/deque of numbers)."""
    if isinstance(obj, deque):
        return sys.getsizeof(obj) + sum(sys.getsizeof(x) for x in obj)
    if isinstance(obj, dict):
        return sys.getsizeof(obj) + sum(
            sys.getsizeof(k) + sys.getsizeof(v) for k, v in obj.items()
        )
    if isinstance(obj, (list, tuple)):
        return sys.getsizeof(obj) + sum(sys.getsizeof(x) for x in obj)
    return sys.getsizeof(obj)
 
 
def build_all(limit: int, window: float):
    """Factory: one fresh instance of each algorithm, sharing the same limit/window."""
    return [
        FixedWindowCounter(limit, window),
        SlidingWindowLog(limit, window),
        SlidingWindowCounter(limit, window),
        TokenBucket(limit, window),
    ]
