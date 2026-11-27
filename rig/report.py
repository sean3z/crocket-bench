import json, statistics as st, collections, os
import os
RAW = os.environ.get("RAW", "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/results/run-1/raw")
rows = [json.loads(l) for l in open(f"{RAW}/steps.jsonl") if l.strip()]
idle = {}
for l in open(f"{RAW}/idle.jsonl"):
    j = json.loads(l); idle[j["app"]] = j["m"]["rss_avg_kb"]
cold = collections.defaultdict(list)
for l in open(f"{RAW}/coldstart.jsonl"):
    j = json.loads(l)
    if "cold_ms" in j: cold[j["app"]].append(j["cold_ms"])
g = collections.defaultdict(list)
for r in rows: g[(r["test"], r["app"], r["label"].split("_")[0])].append(r)
def med(xs):
    xs = [x for x in xs if x is not None]; return st.median(xs) if xs else None
def rng(xs):
    xs = [x for x in xs if x is not None]; return f"{min(xs):,.0f}–{max(xs):,.0f}" if xs else "–"
print("# crocket-bench run 1: production track\n")
print("wrk2 -t4 -c128, HTTP/1.1 keep-alive. Max = median of 3 × 30 s at an unreachable rate after a 60 s warmup at 20k req/s. Latency = median of 3 × 30 s at a common fixed rate (coordinated-omission corrected). CPU µs/req = app user+sys CPU ÷ req/s. Errors = non-2xx + socket errors summed over the 6 measured runs.\n")
for test in sorted({k[0] for k in g}):
    print(f"## {test}\n")
    print("| Framework | Max req/s (median) | range | CPU µs/req at max | App cores at max | Postgres CPU % | Common rate | p50 ms | p99 ms | p99.9 ms | Peak RSS MB | Idle RSS MB | Errors |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    apps = sorted({k[1] for k in g if k[0] == test}, key=lambda a: -(med([r["req_s"] for r in g[(test, a, "max")]]) or 0))
    for a in apps:
        mx, cm = g[(test, a, "max")], g[(test, a, "common")]
        err = sum(r.get("non2xx", 0) + r.get("socket_errors", 0) for r in mx + cm)
        peak = max([r.get("rss_peak_kb", 0) for r in mx + cm] or [0]) / 1024
        f = lambda k, rs=cm: med([r.get(k) for r in rs])
        print(f"| {a} | {med([r['req_s'] for r in mx]) or 0:,.0f} | {rng([r['req_s'] for r in mx])} | {f('cpu_us_per_req', mx) or 0:.0f} | {f('app_cpu_cores', mx) or 0:.2f} | {f('db_cpu_pct', mx) or 0:.0f} | {cm[0]['target_rate'] if cm else '–':,} | {f('p50_ms') or 0:.2f} | {f('p99_ms') or 0:.2f} | {f('p99_9_ms') or 0:.2f} | {peak:.0f} | {idle.get(a, 0)/1024:.0f} | {err} |")
    print()
print("## Cold start (container start to first 200 on /db)\n")
for a, v in cold.items(): print(f"- {a}: {st.median(v)} ms (n={len(v)})")
