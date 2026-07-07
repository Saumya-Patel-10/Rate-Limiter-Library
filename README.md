# Rate Limiter Library
 
Four rate-limiting algorithms behind one interface, plus a traffic generator and
a harness that measures correctness and memory empirically — so the trade-offs
are *demonstrated*, not asserted.
 
```
rate_limiters.py   the four algorithms + common RateLimiter interface
traffic.py         steady / bursty / adversarial traffic generators
simulate.py        runs everything, prints the table, writes comparison.png
tests.py           hand-crafted unit tests (incl. a direct proof of the boundary flaw)
comparison.png     the generated chart
```
 
Run it:
 
```bash
python3 tests.py       # correctness checks
python3 simulate.py    # comparison table + comparison.png
```
 
## The common interface
 
Every algorithm implements `allow(client_id, timestamp) -> bool`. The caller
supplies the timestamp (seconds), so nothing reads the wall clock — that makes
the whole thing deterministic and testable. Per-client state lives in one hash
map (`client_id -> state`); swap any limiter in and the harness treats it the
same.
 
## The algorithms and their trade-offs
 
**1. Fixed Window Counter** — one `(window_index, count)` per client, reset each
window. O(1) memory, trivial. Its flaw is real and reproducible: a full burst at
`0:59` and another at `1:00` land in two different buckets, so up to `2x` the
limit slips through in a span shorter than one window.
 
**2. Sliding Window Log** — a deque of every timestamp per client; evict the
front once it's older than `now - W`, then count what's left (classic
two-pointer). Exact — it's the ground truth the others are scored against — but
memory grows with in-window request volume (up to `limit` timestamps per
client).
 
**3. Sliding Window Counter** — keeps just the current and previous window
counts and weights the previous one by how much it still overlaps the trailing
window: `estimate = cur + prev * (W - elapsed)/W`. O(1) memory, and its
boundary behavior is far tighter than the fixed window. The approximation
assumes requests were spread evenly across the previous window, so it can be
slightly off, but it's the sweet spot for most systems and the best one to
explain deeply in an interview.
 
**4. Token Bucket** — a bucket that refills at `limit/window` tokens/sec up to a
capacity; each request spends a token. O(1) memory, and unlike the window
approaches it *allows controlled bursts*: a client that's been quiet can spend
its whole bucket at once, then is throttled to the refill rate.
 
## What the simulation shows
 
Numbers from `simulate.py` (100 req / 60s, 50 clients, 600s), reported as
**overshoot** = most requests admitted in any true sliding window ÷ limit
(1.00x = perfect, 2.00x = leaked double):
 
| pattern      | Fixed Window | Sliding Log | Sliding Counter | Token Bucket |
|--------------|:------------:|:-----------:|:---------------:|:------------:|
| steady       | 1.01x        | 1.00x       | 1.01x           | 1.21x*       |
| bursty       | 1.45x        | 1.00x       | 1.02x           | 1.05x        |
| adversarial  | **2.00x**    | 1.00x       | 1.10x           | 1.10x        |
| peak/client  | 128 B        | ~4 KB       | 402 B           | 120 B        |
 
\* Token Bucket's steady "overshoot" isn't a bug — it's the intentional burst
allowance: an idle client arrives with a full bucket and spends it at once.
 
The headline: **Fixed Window leaks to exactly 2.00x under the boundary attack**,
Sliding Log is exact but ~30x the per-client memory, and Sliding Counter buys
nearly-exact behavior for a tiny constant footprint.
 
## Porting to C++/Java
 
The interface is deliberately thin. `RateLimiter` -> an abstract base /
interface with `bool allow(...)`; the per-client `dict` -> `unordered_map` /
`HashMap`; the log's `deque` -> `std::deque` / `ArrayDeque`. The algorithm
bodies translate almost line for line.
