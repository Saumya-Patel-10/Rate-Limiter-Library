"""
Traffic generators.  Each returns a list of (client_id, timestamp) events
SORTED by timestamp -- exactly what a real limiter would see off the wire.
 
Patterns
--------
steady       : every client fires at a constant interval (a chosen multiple of
               the limit) for the whole run. Tests baseline correctness.
bursty       : clients are idle, then dump a clump of requests, then idle again.
               Tests how gracefully each algorithm handles legitimate spikes.
adversarial  : the boundary attack. Every client fires a full `limit` burst in
               the last sliver of one fixed window, then another full `limit`
               burst in the first sliver of the next -- 2*limit requests inside
               a span shorter than one window. This is what exposes the Fixed
               Window flaw instead of us just asserting it.
"""
 
from __future__ import annotations
 
import random
 
 
def steady(n_clients: int, window: float, limit: int,
           duration: float, load: float = 1.2, seed: int = 0):
    """
    Each client sends at `load * limit` requests per window (load=1.2 -> 20%
    over the cap, so a correct limiter should reject ~1/6 of them).
    A small per-client phase offset keeps them from being perfectly aligned.
    """
    rng = random.Random(seed)
    per_window = load * limit
    interval = window / per_window
    events = []
    for c in range(n_clients):
        phase = rng.uniform(0, interval)
        t = phase
        while t < duration:
            events.append((c, t))
            t += interval
    events.sort(key=lambda e: e[1])
    return events
 
 
def bursty(n_clients: int, window: float, limit: int,
           duration: float, burst_size: int | None = None,
           idle_windows: float = 2.0, seed: int = 1):
    """
    Each client alternates: sit idle for `idle_windows` windows, then fire a
    tight burst of `burst_size` requests (default = 1.5x limit, i.e. an
    oversized spike) over ~5% of a window. Oversizing the burst is what makes
    the algorithms diverge -- a full bucket / fresh window absorbs part of it,
    the rest gets rejected.
    """
    rng = random.Random(seed)
    burst_size = burst_size or int(1.5 * limit)
    spread = 0.05 * window
    gap = idle_windows * window
    events = []
    for c in range(n_clients):
        t = rng.uniform(0, gap)          # stagger first bursts
        while t < duration:
            for i in range(burst_size):
                ts = t + (i / max(burst_size - 1, 1)) * spread
                if ts < duration:
                    events.append((c, ts))
            t += gap
    events.sort(key=lambda e: e[1])
    return events
 
 
def adversarial(n_clients: int, window: float, limit: int,
                duration: float, seed: int = 2):
    """
    Boundary attack. For each fixed window boundary B = k*W, each client sends:
        - `limit` requests just BEFORE B (in [B - eps, B))
        - `limit` requests just AFTER  B (in [B, B + eps))
    so 2*limit requests land inside a 2*eps span << W, straddling the seam
    between two fixed windows. A true sliding window sees a violation; the fixed
    window counts them in two separate buckets and lets them all through.
    """
    rng = random.Random(seed)
    eps = 0.01 * window
    events = []
    boundary = window
    while boundary < duration:
        for c in range(n_clients):
            jitter = rng.uniform(0, eps * 0.1)
            for i in range(limit):                       # trailing burst
                events.append((c, boundary - eps + (i / limit) * eps + jitter))
            for i in range(limit):                       # leading burst
                events.append((c, boundary + (i / limit) * eps + jitter))
        boundary += window
    events = [(c, t) for (c, t) in events if 0 <= t < duration]
    events.sort(key=lambda e: e[1])
    return events
 
 
GENERATORS = {
    "steady": steady,
    "bursty": bursty,
    "adversarial": adversarial,
}
