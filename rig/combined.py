# Combined results across tier 1 (raw/) and tiers 2-3 (raw-t23/). Where an app+test was measured in both,
# the later run (raw-t23) is used in the main table and the earlier one is listed separately.
import json, statistics as st, collections, sys
BASE = "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/results/run-1"
def load(d):
    g = collections.defaultdict(list)
    for l in open(f"{BASE}/{d}/steps.jsonl"):
        if l.strip():
            r = json.loads(l)
            if r["label"] != "warmup": g[(r["test"], r["app"], r["label"].rsplit("_", 1)[0])].append(r)
    idle, cold = {}, collections.defaultdict(list)
    for l in open(f"{BASE}/{d}/idle.jsonl"):
        j = json.loads(l); idle[j["app"]] = j["m"]["rss_avg_kb"] / 1024
    for l in open(f"{BASE}/{d}/coldstart.jsonl"):
        j = json.loads(l)
        if "cold_ms" in j: cold[j["app"]].append(j["cold_ms"])
    return g, idle, cold
t1, idle1, cold1 = load("raw"); t2, idle2, cold2 = load("raw-t23")
def med(rs, k):
    v = [r[k] for r in rs if r.get(k) is not None]; return st.median(v) if v else None
def f(v, fmt): return "–" if v is None else format(v, fmt)
def table(g, test, src):
    apps = {k[1] for k in g if k[0] == test}
    rows = []
    for a in apps:
        mx, cm = g.get((test, a, "max"), []), g.get((test, a, "common"), [])
        if not mx: continue
        err = sum(r.get("non2xx", 0) + r.get("socket_errors", 0) for r in mx + cm)
        peak = max(r.get("rss_peak_kb", 0) for r in mx + cm) / 1024
        rows.append((med(mx, "req_s") or 0, a, mx, cm, err, peak))
    rows.sort(reverse=True)
    out = []
    for q, a, mx, cm, err, peak in rows:
        out.append(f"| {a} | {q:,.0f} | {min(r['req_s'] for r in mx):,.0f}–{max(r['req_s'] for r in mx):,.0f} | {f(med(mx,'cpu_us_per_req'),'.0f')} | {f(med(mx,'app_cpu_cores'),'.2f')} | {f(med(mx,'lg_cpu_pct'),'.0f')} | {f(med(mx,'db_cpu_pct'),'.0f')} | {f(med(mx,'p50_ms'),',.0f')} / {f(med(mx,'p99_ms'),',.0f')} | {f(med(cm,'req_s'),',.0f')} | {f(med(cm,'p50_ms'),'.2f')} | {f(med(cm,'p99_ms'),'.2f')} | {f(med(cm,'cpu_us_per_req'),'.0f')} | {peak:.0f} | {err} | {src} |")
    return out
H = "| Framework | Max req/s (median of 3) | range | CPU µs/req at max | App cores at max | Loadgen CPU % at max | Postgres CPU % at max | p50 / p99 ms at max (queueing) | Common req/s | p50 ms | p99 ms | CPU µs/req at common | Peak RSS MB | Errors | Run |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
print("# crocket-bench run 1: combined results (production track)\n")
print("3 × DigitalOcean c-4 (app / Postgres 16 / wrk2 load generator), nyc1. wrk2 -t4 -c128, HTTP/1.1 keep-alive, no pipelining. Max = median of 3 × 30 s at an unreachable rate after a 60 s warmup at 20k req/s; latency at max is queueing and only shown for completeness. Common rate = 10k req/s (plaintext, json) or 4k req/s (db), median of 3 × 30 s, wrk2 coordinated-omission corrected. Run: T1 = tier 1 droplets (08:04–09:14 ET), T23 = tiers 2-3 droplets (09:44 ET onward). Loadgen CPU wasn't recorded in T1.\n")
for test in ("plaintext", "json", "db"):
    rows = table(t2, test, "T23")
    have = {r.split("|")[1].strip() for r in rows}
    for r in table(t1, test, "T1"):
        if r.split("|")[1].strip() not in have: rows.append(r)
    rows.sort(key=lambda r: -float(r.split("|")[2].replace(",", "")))
    print(f"## {test}\n\n{H}"); print("\n".join(rows)); print()
    dup = [r for r in table(t1, test, "T1 (superseded)") if r.split("|")[1].strip() in have]
    if dup: print(f"Earlier tier-1 measurement of the same {test} test (kept for comparison):\n\n{H}\n" + "\n".join(dup) + "\n")
print("## Idle RSS and cold start\n\n| Framework | Idle RSS MB | Cold start ms (container start to first 200 on /db) | Run |\n|---|---|---|---|")
for a in sorted(set(idle1) | set(idle2)):
    src = "T23" if a in idle2 else "T1"; i = idle2.get(a, idle1.get(a)); c = cold2.get(a) or cold1.get(a)
    print(f"| {a} | {i:.0f} | {st.median(c) if c else '–'} (n={len(c) if c else 0}) | {src} |")
