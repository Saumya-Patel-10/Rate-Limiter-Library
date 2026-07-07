"""
Minimal test harness. Run:  python3 tests.py
Each test constructs hand-crafted timestamps so the expected decision is obvious,
which is easier to reason about than the statistical simulation.
"""
 
from rate_limiters import (
    FixedWindowCounter, SlidingWindowLog, SlidingWindowCounter, TokenBucket
)
 
LIMIT, W = 5, 10.0     # small numbers keep the assertions readable
 
 
def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    assert cond, name
 
 
def test_fixed_window_basic():
    print("fixed window: basic count + reset")
    rl = FixedWindowCounter(LIMIT, W)
    allowed = [rl.allow("a", t) for t in [0, 1, 2, 3, 4, 5]]  # 6 in [0,10)
    check("first 5 allowed", allowed[:5] == [True] * 5)
    check("6th rejected", allowed[5] is False)
    check("resets in next window", rl.allow("a", 11) is True)
 
 
def test_fixed_window_boundary_flaw():
    print("fixed window: the 2x boundary flaw")
    rl = FixedWindowCounter(LIMIT, W)
    # 5 requests just before the boundary, 5 just after -> 10 within ~0.2s
    for t in [9.8, 9.85, 9.9, 9.95, 9.99]:
        assert rl.allow("a", t)
    admitted_after = sum(rl.allow("a", t) for t in [10.0, 10.05, 10.1, 10.15, 10.2])
    check("all 5 post-boundary also admitted (10 in <1s!)", admitted_after == 5)
 
 
def test_sliding_log_exact():
    print("sliding log: exact eviction")
    rl = SlidingWindowLog(LIMIT, W)
    for t in [0, 1, 2, 3, 4]:
        assert rl.allow("a", t)
    check("6th within window rejected", rl.allow("a", 5) is False)
    # by t=10.1 the t=0 entry has expired -> room for exactly one more
    check("one slot frees after oldest expires", rl.allow("a", 10.1) is True)
    check("still capped right after", rl.allow("a", 10.2) is False)
 
 
def test_sliding_log_blocks_boundary_attack():
    print("sliding log: boundary attack is caught")
    rl = SlidingWindowLog(LIMIT, W)
    for t in [9.8, 9.85, 9.9, 9.95, 9.99]:
        assert rl.allow("a", t)
    admitted_after = sum(rl.allow("a", t) for t in [10.0, 10.05, 10.1, 10.15, 10.2])
    check("post-boundary burst rejected (unlike fixed)", admitted_after == 0)
 
 
def test_sliding_counter_approximates():
    print("sliding counter: stays near the limit at the boundary")
    rl = SlidingWindowCounter(LIMIT, W)
    for t in [9.8, 9.85, 9.9, 9.95, 9.99]:
        assert rl.allow("a", t)
    admitted_after = sum(rl.allow("a", t) for t in [10.0, 10.05, 10.1, 10.15, 10.2])
    # weight of previous window ~1.0 just after boundary -> should admit ~0
    check("admits far fewer than fixed's 5", admitted_after <= 1)
 
 
def test_token_bucket_burst_then_throttle():
    print("token bucket: burst up to capacity, then refill-limited")
    rl = TokenBucket(LIMIT, W)                 # rate = 0.5 tok/s, capacity = 5
    burst = sum(rl.allow("a", 0.0) for _ in range(8))   # all at t=0
    check("initial burst == capacity (5)", burst == 5)
    check("no token yet at t=1", rl.allow("a", 1.0) is False)
    check("one token refilled by t=2 (0.5/s)", rl.allow("a", 2.0) is True)
 
 
def test_independent_clients():
    print("clients are isolated")
    rl = FixedWindowCounter(LIMIT, W)
    assert all(rl.allow("a", t) for t in range(5))
    check("client a exhausted", rl.allow("a", 5) is False)
    check("client b unaffected", rl.allow("b", 5) is True)
 
 
if __name__ == "__main__":
    for fn in [
        test_fixed_window_basic,
        test_fixed_window_boundary_flaw,
        test_sliding_log_exact,
        test_sliding_log_blocks_boundary_attack,
        test_sliding_counter_approximates,
        test_token_bucket_burst_then_throttle,
        test_independent_clients,
    ]:
        fn()
    print("\nall tests passed")
