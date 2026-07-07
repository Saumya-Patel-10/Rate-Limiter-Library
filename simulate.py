"""
Run every algorithm against every traffic pattern, then report:
 
  allowed / rejected      : raw decisions
  worst-window            : the MOST requests any single client got admitted
                            inside a true sliding W-second window. This is the
                            real correctness test -- anything above `limit` means
                            the limiter over-admitted (leaked).
  overshoot               : worst-window / limit  (1.00 = perfect, 2.00 = 2x leak)
  peak bytes/client       : worst-case per-client state footprint
  total bytes             : resident state across all clients at end of run
 
Produces a printed table and a PNG chart.
"""
 
from __future__ import annotations
 
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
 
from rate_limiters import build_all
from traffic import GENERATORS
 
LIMIT = 100
WINDOW = 60.0          # seconds
DURATION = 600.0       # 10 windows
N_CLIENTS = 50
 
 
def run_limiter(limiter, events):
    """Feed events through a limiter, capturing decisions and peak memory."""
    allowed = []          # (client_id, timestamp) that were admitted
    n_allowed = 0
    peak_total = 0
    for i, (cid, ts) in enumerate(events):
        if limiter.allow(cid, ts):
            allowed.append((cid, ts))
            n_allowed += 1
        if i % 200 == 0:                       # sample memory periodically
            peak_total = max(peak_total, limiter.state_bytes())
    peak_total = max(peak_total, limiter.state_bytes())
    return {
        "allowed": n_allowed,
        "rejected": len(events) - n_allowed,
        "allowed_events": allowed,
        "peak_client_bytes": limiter.peak_state(),
        "total_bytes": peak_total,
    }
 
 
def worst_true_window(allowed_events, window, limit):
    """
    Ground truth. For each client, the max number of ADMITTED requests inside
    any sliding window of length `window` (two-pointer). Returns the global worst
    across all clients -- the true peak admission rate the limiter permitted.
    """
    by_client: dict = {}
    for cid, ts in allowed_events:
        by_client.setdefault(cid, []).append(ts)
    worst = 0
    for times in by_client.values():
        times.sort()
        left = 0
        for right in range(len(times)):
            while times[right] - times[left] >= window:
                left += 1
            worst = max(worst, right - left + 1)
    return worst
 
 
def fmt_bytes(n):
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"


# --------------------------------------------------------------------------- #
# console table rendering
# --------------------------------------------------------------------------- #
def _column_widths(headers, rows):
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    return [w + 2 for w in widths]   # 1 space of padding on each side


def table_width(headers, rows):
    """Total printed width of the table (borders included) for this header/rows set."""
    return len(_column_widths(headers, rows)) + 1 + sum(_column_widths(headers, rows))


def print_table(headers, rows, aligns=None):
    """Print a clean box-drawn table (auto-sized columns, unicode borders)."""
    n_cols = len(headers)
    aligns = aligns or (["<"] + [">"] * (n_cols - 1))
    widths = _column_widths(headers, rows)

    def rule(left, mid, right):
        return left + mid.join("─" * w for w in widths) + right

    def fmt_row(cells):
        parts = [f" {str(c):{a}{w - 2}} " for c, w, a in zip(cells, widths, aligns)]
        return "│" + "│".join(parts) + "│"

    print(rule("┌", "┬", "┐"))
    print(fmt_row(headers))
    print(rule("├", "┼", "┤"))
    for row in rows:
        print(fmt_row(row))
    print(rule("└", "┴", "┘"))


def section_title(text, width):
    """Centered section header, e.g. '── steady (60000 events) ──'."""
    label = f" {text} "
    if len(label) >= width:
        print(label)
        return
    side = (width - len(label)) // 2
    print("─" * side + label + "─" * (width - side - len(label)))


def main():
    patterns = ["steady", "bursty", "adversarial"]
    results = {}   # (pattern, algo_name) -> metrics
 
    for pat in patterns:
        events = GENERATORS[pat](N_CLIENTS, WINDOW, LIMIT, DURATION)
        for limiter in build_all(LIMIT, WINDOW):
            r = run_limiter(limiter, events)
            r["worst_window"] = worst_true_window(r["allowed_events"], WINDOW, LIMIT)
            r["overshoot"] = r["worst_window"] / LIMIT
            r["n_events"] = len(events)
            results[(pat, limiter.name)] = r
 
    algos = ["Fixed Window", "Sliding Log", "Sliding Counter", "Token Bucket"]
 
    # -------------------- printed table --------------------
    banner = f"Limit = {LIMIT} req / {WINDOW:.0f}s window  |  {N_CLIENTS} clients  |  {DURATION:.0f}s run"
    print()
    print("═" * len(banner))
    print(banner)
    print("═" * len(banner))

    headers = ["Algorithm", "Allowed", "Rejected", "WorstWin", "Overshoot", "Peak/Client", "Total"]
    aligns = ["<", ">", ">", ">", ">", ">", ">"]

    for pat in patterns:
        n_ev = results[(pat, algos[0])]["n_events"]
        rows = []
        for a in algos:
            r = results[(pat, a)]
            rows.append([
                a,
                r["allowed"],
                r["rejected"],
                r["worst_window"],
                f"{r['overshoot']:.2f}x",
                fmt_bytes(r["peak_client_bytes"]),
                fmt_bytes(r["total_bytes"]),
            ])
        print()
        section_title(f"{pat}  ({n_ev} events)", table_width(headers, rows))
        print_table(headers, rows, aligns)

    print("\nworstWin = most admitted requests in ANY true sliding window "
          "(<=100 is correct; higher = leaked).")
 
    make_chart(results, patterns, algos)
    return results
 
 
def make_chart(results, patterns, algos):
    colors = {
        "Fixed Window": "#e06c5a",
        "Sliding Log": "#5b8def",
        "Sliding Counter": "#37b679",
        "Token Bucket": "#b07de0",
    }
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
 
    # --- left: correctness (overshoot) grouped by pattern ---
    ax = axes[0]
    x = range(len(patterns))
    w = 0.2
    for j, a in enumerate(algos):
        vals = [results[(p, a)]["overshoot"] for p in patterns]
        offs = [i + (j - 1.5) * w for i in x]
        ax.bar(offs, vals, w, label=a, color=colors[a])
    ax.axhline(1.0, ls="--", c="#444", lw=1)
    ax.text(len(patterns) - 0.5, 1.02, "limit (1.0x)", fontsize=8, color="#444")
    ax.set_xticks(list(x))
    ax.set_xticklabels(patterns)
    ax.set_ylabel("overshoot  (worst true-window / limit)")
    ax.set_title("Correctness: how far each limiter leaks past the cap")
    ax.legend(fontsize=8)
    ax.grid(axis="y", alpha=0.3)
 
    # --- right: peak per-client memory (log scale) ---
    ax = axes[1]
    pat = "adversarial"    # heaviest load -> clearest memory story
    vals = [results[(pat, a)]["peak_client_bytes"] for a in algos]
    bars = ax.bar(algos, vals, color=[colors[a] for a in algos])
    ax.set_yscale("log")
    ax.set_ylabel("peak per-client state (bytes, log scale)")
    ax.set_title(f"Memory: worst-case state per client [{pat}]")
    ax.tick_params(axis="x", rotation=20)
    for b, v in zip(bars, vals):
        ax.text(b.get_x() + b.get_width() / 2, v, fmt_bytes(v),
                ha="center", va="bottom", fontsize=8)
    ax.grid(axis="y", alpha=0.3)
 
    fig.suptitle("Rate limiter comparison: correctness vs memory", fontweight="bold")
    fig.tight_layout()
    fig.savefig("comparison.png", dpi=130, bbox_inches="tight")
    print("\nchart written to comparison.png")
 
 
if __name__ == "__main__":
    main()
