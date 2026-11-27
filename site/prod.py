"""Collect and render the production-behavior tests (drain, slow clients,
oversized bodies, connection churn, memory soak, contract)."""
from __future__ import annotations

import json
import re
import statistics as st
from collections import defaultdict
from pathlib import Path

# Imported from build.py at use time for helpers and catalog, to avoid a cycle
# at import. These names are bound in attach().

e = fmt_int = fmt_dec = med = FRAMEWORKS = load_jsonl = None

CONTRACT_CASES = [
    ("ok", "Valid body"),
    ("wrong_type", "Wrong type"),
    ("missing", "Missing field"),
    ("extra", "Extra field"),
    ("malformed", "Malformed JSON"),
]

PROD_BLURBS = {
    "drain": (
        "Sixteen GET /db requests, each spending 500 ms waiting on Postgres, then SIGTERM 150 ms later and no new traffic after that, "
        "the way a load balancer pulls a pod. Three runs each. This is whether a deploy drops the requests already in progress"
    ),
    "slow": (
        "Sixteen clients trickled incomplete headers for about 25 seconds, while healthy "
        "GET /json traffic kept going. I wanted to see if a slow connection can tie the server up"
    ),
    "oversize": (
        "A 10 MB and a 100 MB POST to /echo. Without a cap, a body like that can run a process out of memory"
    ),
    "churn": (
        "Same JSON test, but every request opens a new connection. That's closer to a client "
        "that doesn't keep-alive, or a proxy that churns"
    ),
    "soak": (
        "Ten minutes at 3,500 req/s, with RSS sampled every 5 seconds. I wanted to see if memory sits still or keeps climbing"
    ),
    "contract": (
        "POST /echo with a name and a quantity, then a wrong type, a missing field, an extra field, "
        "and broken JSON. This is what a client actually gets back, using each framework's default"
    ),
}

SERIES_CLASS = {
    "crocket": "series-crocket",
    "rocket": "series-rocket",
    "axum": "series-axum",
    "drogon": "series-drogon",
    "fastify": "series-fastify",
    "spring": "series-spring",
    "fastapi": "series-fastapi",
    "hyperexpress": "series-hyperexpress",
}


def attach(ns):
    global e, fmt_int, fmt_dec, med, FRAMEWORKS, load_jsonl
    e = ns["e"]
    fmt_int = ns["fmt_int"]
    fmt_dec = ns["fmt_dec"]
    med = ns["med"]
    FRAMEWORKS = ns["FRAMEWORKS"]
    load_jsonl = ns["load_jsonl"]


def fw_name(fid):
    meta = (FRAMEWORKS or {}).get(fid) or {}
    return meta.get("name") or fid


def last_json(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return None
    text = path.read_text().strip()
    if not text:
        return None
    # Some drain files append a new object per run.
    for chunk in reversed(text.splitlines()):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            continue
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def parse_wrk_text(txt: str):
    def to_ms(v, u):
        return float(v) * {"us": 1e-3, "ms": 1, "s": 1e3, "m": 6e4}[u]

    r = {}
    for p in ("50.000", "90.000", "99.000", "99.900"):
        mm = re.search(r"^\s*%s%%\s+([\d.]+)(us|ms|s|m)\b" % re.escape(p), txt, re.M)
        if mm:
            key = "p" + p.split(".")[0] + ("_9" if p == "99.900" else "") + "_ms"
            r[key] = round(to_ms(*mm.groups()), 3)
    mm = re.search(r"Requests/sec:\s+([\d.]+)", txt)
    r["req_s"] = float(mm.group(1)) if mm else None
    mm = re.search(r"(\d+) requests in", txt)
    r["requests"] = int(mm.group(1)) if mm else None
    mm = re.search(r"Non-2xx or 3xx responses:\s+(\d+)", txt)
    r["non2xx"] = int(mm.group(1)) if mm else 0
    mm = re.search(r"Socket errors: connect (\d+), read (\d+), write (\d+), timeout (\d+)", txt)
    r["socket_errors"] = sum(map(int, mm.groups())) if mm else 0
    return r


def parse_wrk(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        return None
    parsed = parse_wrk_text(path.read_text())
    if parsed.get("req_s") is None and parsed.get("requests") is None:
        return None
    return parsed


def verdict_for(case, rec):
    if not rec:
        return "missing", "–", None
    status = rec.get("status")
    err = rec.get("error")
    cls = rec.get("class")
    label = "–" if status is None and not err else (str(status) if status is not None else str(err))
    if cls == "rejected_clear" or (isinstance(status, int) and 400 <= status < 500):
        return "rejected", label, status
    if case == "ok" and isinstance(status, int) and 200 <= status < 300:
        return "ok", label, status
    if cls == "accepted_garbage" or (case != "ok" and isinstance(status, int) and 200 <= status < 300):
        return "accepted", label, status
    if isinstance(status, int) and status >= 500:
        return "failed", label, status
    if err:
        # Connection reset / timeout on a huge body is still a refusal, just a messy one
        low = str(err).lower()
        if "reset" in low or "broken pipe" in low or "timeout" in low:
            return "reset", label, status
        return "error", label, status
    if status == 404:
        return "missing", label, status
    return "other", label, status


def summarize_probes(obj):
    fw = obj.get("app")
    contract = obj.get("contract") or {}
    cases = {}
    for key, _title in CONTRACT_CASES:
        rec = contract.get(key) or {}
        verdict, label, status = verdict_for(key, rec)
        cases[key] = {
            "verdict": verdict,
            "label": label,
            "status": status,
            "ms": rec.get("ms"),
            "error": rec.get("error"),
        }
    # Skip a file that never hit /echo (old image).
    if cases.get("ok", {}).get("status") == 404 or cases.get("ok", {}).get("verdict") == "missing":
        if all(c.get("status") == 404 or c.get("verdict") == "missing" for c in cases.values()):
            return None
    slow = obj.get("slow") or {}
    over = obj.get("oversize") or {}
    over_out = {}
    for size in ("10mb", "100mb"):
        rec = over.get(size) or {}
        verdict, label, status = verdict_for("oversize", rec)
        over_out[size] = {
            "verdict": verdict,
            "label": label,
            "status": status,
            "ms": rec.get("ms"),
            "error": rec.get("error"),
            "bytes": rec.get("bytes"),
        }
    return {
        "framework": fw,
        "contract": cases,
        "slow": {
            "opened": slow.get("opened"),
            "stillOpen": slow.get("still_open_at_end"),
            "medianCloseS": slow.get("median_close_s"),
            "healthyOk": slow.get("healthy_ok"),
            "healthyErr": slow.get("healthy_err"),
            "healthyP50Ms": slow.get("healthy_p50_ms"),
            "healthyP99Ms": slow.get("healthy_p99_ms"),
            "closeS": slow.get("close_s") or [],
        },
        "oversize": over_out,
    }


def summarize_drain(obj):
    load = obj.get("load") or {}
    ok = load.get("ok")
    err = load.get("err")
    sig = obj.get("sigterm_unix")
    down = obj.get("down_unix")
    up = obj.get("up_unix")
    # Incomplete cut-off runs have empty load/stop.
    if sig is None and not load:
        return None
    stop_s = None if sig is None or down is None else max(0, down - sig)
    restart_s = None if down is None or up is None else max(0, up - down)
    return {
        "framework": obj.get("app"),
        "ok": ok,
        "err": err,
        "seconds": load.get("seconds"),
        "by": load.get("by") or {},
        "stopS": stop_s,
        "restartS": restart_s,
    }


def summarize_soak(rss_path: Path, wrk_path: Path, fw: str):
    series = []
    for row in load_jsonl(rss_path):
        t = row.get("t")
        kb = row.get("rss_kb")
        if t is None or kb is None:
            continue
        series.append({"t": t, "mb": round(kb / 1024, 1)})
    wrk = parse_wrk(wrk_path)
    if not series and not wrk:
        return None
    start = series[0]["mb"] if series else None
    end = series[-1]["mb"] if series else None
    peak = max((p["mb"] for p in series), default=None)
    delta = None if start is None or end is None else round(end - start, 1)
    out = {
        "framework": fw,
        "series": series,
        "startMb": start,
        "endMb": end,
        "peakMb": peak,
        "deltaMb": delta,
        "samples": len(series),
    }
    if wrk:
        out["reqS"] = wrk.get("req_s")
        out["p99Ms"] = wrk.get("p99_ms")
        out["p50Ms"] = wrk.get("p50_ms")
        out["non2xx"] = wrk.get("non2xx")
        out["socketErrors"] = wrk.get("socket_errors")
        out["requests"] = wrk.get("requests")
    return out


def summarize_churn(steps):
    # Later rows win; take the last three churn_max runs per app.
    by_app = defaultdict(list)
    for row in steps:
        if row.get("test") != "json":
            continue
        label = str(row.get("label") or "")
        if not label.startswith("churn_max"):
            continue
        if row.get("req_s") is None:
            continue
        by_app[row["app"]].append(row)
    out = []
    for fw, rows in by_app.items():
        mx = rows[-3:]
        rps = [r["req_s"] for r in mx]
        p99 = [r.get("p99_ms") for r in mx]
        rss = [r.get("rss_peak_kb") or 0 for r in mx]
        out.append({
            "framework": fw,
            "maxRps": round(med(rps)),
            "maxRpsRange": [round(min(rps)), round(max(rps))],
            "p99Ms": None if med(p99) is None else round(med(p99), 2),
            "peakRssMb": round(max(rss) / 1024) if rss else None,
            "non2xx": sum((r.get("non2xx") or 0) for r in mx),
            "socketErrors": sum((r.get("socket_errors") or 0) for r in mx),
        })
    return out


def summarize_inflight(rows):
    rows = [r for r in rows if "inFlightOk" in r]
    if not rows:
        return None
    n = sum(r.get("n") or 0 for r in rows)
    ok = sum(r["inFlightOk"] for r in rows)
    clean = sum(1 for r in rows if r.get("cleanExit"))
    exits = [r["exitS"] for r in rows if r.get("exitS") is not None]
    return {
        "framework": rows[0]["app"],
        "runs": len(rows),
        "perRun": rows[0].get("n"),
        "ok": ok,
        "total": n,
        "dropped": n - ok,
        "cleanRuns": clean,
        "exitS": None if not exits else round(med(exits), 2),
        "graceS": rows[0].get("graceS"),
        "termAfterMs": rows[0].get("termAfterMs"),
        "exitCodes": sorted({str(r.get("exitCode")) for r in rows}),
    }


def _soak_drift(row):
    series = row.get("series") or []
    after = [p for p in series if p["t"] >= 60]
    if len(after) < 2:
        return row.get("deltaMb")
    return round(after[-1]["mb"] - after[0]["mb"], 1)


OVER_SCORE = {"rejected": 2, "reset": 1, "accepted": 0}


def collect_prod(raw_dirs):
    probes = {}
    inflight = defaultdict(list)
    soak = {}
    steps = []
    for raw in raw_dirs:
        for p in sorted(raw.glob("probes_*.json")):
            fw = p.stem[len("probes_"):]
            obj = last_json(p)
            if not obj:
                continue
            summary = summarize_probes(obj)
            if summary:
                summary["framework"] = summary.get("framework") or fw
                probes[summary["framework"]] = summary
        for row in load_jsonl(raw / "drain_inflight.jsonl"):
            if row.get("app"):
                inflight[row["app"]].append(row)
        for p in sorted(raw.glob("soak_rss_*.jsonl")):
            fw = p.stem[len("soak_rss_"):]
            wrk = raw / f"wrk_{fw}_json_soak.txt"
            summary = summarize_soak(p, wrk, fw)
            if summary:
                soak[fw] = summary
        steps.extend(load_jsonl(raw / "steps.jsonl"))
    churn_rows = summarize_churn(steps)
    show = list(FRAMEWORKS)
    drain = {}
    for fw, rows in inflight.items():
        sm = summarize_inflight(rows)
        if sm:
            drain[fw] = sm
    for fw, row in soak.items():
        row["driftMb"] = _soak_drift(row)

    def bad_rejected(r):
        c = r.get("contract") or {}
        return sum(1 for k in ("wrong_type", "missing", "extra", "malformed")
                   if (c.get(k) or {}).get("verdict") == "rejected")

    def over_key(r):
        o = r.get("oversize") or {}
        score = sum(OVER_SCORE.get((o.get(sz) or {}).get("verdict"), 0) for sz in ("10mb", "100mb"))
        return (-score, sum(((o.get(sz) or {}).get("ms") or 0) for sz in ("10mb", "100mb")))

    def slow_key(r):
        sl = r.get("slow") or {}
        return (sl.get("stillOpen") if sl.get("stillOpen") is not None else 99,
                sl.get("medianCloseS") or 99, sl.get("healthyP99Ms") or 99)

    pr = [probes[k] for k in show if k in probes]
    return {
        "contract": sorted(pr, key=lambda r: -bad_rejected(r)),
        "slow": sorted(pr, key=slow_key),
        "oversize": sorted(pr, key=over_key),
        "drain": sorted([drain[k] for k in show if k in drain],
                        key=lambda r: (-(r["ok"] / (r["total"] or 1)), -r["cleanRuns"],
                                       any(c not in ("0", "143") for c in r.get("exitCodes") or []), r["exitS"] or 99)),
        "soak": sorted([soak[k] for k in show if k in soak], key=lambda r: abs(r.get("driftMb") or 0)),
        "churn": sorted([r for r in churn_rows if r["framework"] in show], key=lambda r: -(r.get("maxRps") or 0)),
        "coming": [],
    }


def _order(rows):
    rank = {fid: i for i, fid in enumerate(FRAMEWORKS or {})}
    return sorted(rows, key=lambda r: rank.get(r.get("framework"), 99))


def render_simple_bars(rows, value_key, unit, hidden_label):
    vals = [r.get(value_key) for r in rows if isinstance(r.get(value_key), (int, float))]
    if not vals:
        return ""
    top = max(vals) or 1
    parts = [f'<figure class="bars"><div class="visually-hidden">{e(hidden_label)}</div>']
    for row in sorted(rows, key=lambda r: -(r.get(value_key) or 0)):
        fw = row["framework"]
        val = row.get(value_key)
        if val is None:
            continue
        pct = max(0.5, (val / top) * 100)
        cls = " is-crocket" if fw == "crocket" else ""
        name = fw_name(fw)
        if unit == "req/s":
            shown = f"{fmt_int(val)}<span class=\"unit\">req/s</span>"
        elif unit == "MB":
            shown = f"{fmt_dec(val, 1)} <span class=\"unit\">MB</span>"
        elif unit == "s":
            shown = f"{fmt_dec(val, 1)} <span class=\"unit\">s</span>"
        else:
            shown = f"{fmt_int(val)}<span class=\"unit\">{e(unit)}</span>"
        parts.append(
            '<div class="bar-row">'
            f'<p class="bar-name">{e(name)}</p>'
            '<div class="bar-track">'
            '<svg class="bar-svg" viewBox="0 0 100 1" preserveAspectRatio="none" aria-hidden="true">'
            f'<rect class="bar-fill{cls}" x="0" y="0" width="{pct:.2f}" height="1"></rect>'
            "</svg></div>"
            f'<p class="bar-n">{shown}</p>'
            "</div>"
        )
    parts.append("</figure>")
    return "\n".join(parts)


def soak_chart(rows):
    import math
    series_rows = [r for r in rows if r.get("series")]
    if not series_rows:
        return ""
    max_t = max((p["t"] for r in series_rows for p in r["series"]), default=600) or 600
    lo_mb = min(p["mb"] for r in series_rows for p in r["series"])
    hi_mb = max(p["mb"] for r in series_rows for p in r["series"])
    lo = math.log10(max(1.0, lo_mb * 0.8))
    hi = math.log10(hi_mb * 1.25)
    left, right, top, bottom = 64, 900, 34, 400
    width, height = 960, 440
    plot_w, plot_h = right - left, bottom - top

    def x(t):
        return left + (t / max_t) * plot_w

    def y(mb):
        return bottom - ((math.log10(max(mb, 0.1)) - lo) / (hi - lo)) * plot_h

    parts = [
        '<figure class="soak-chart" data-soak-chart>',
        '<div class="visually-hidden">RSS over a 10 minute soak at 3,500 req/s, in megabytes, log scale</div>',
        f'<svg class="soak-svg" viewBox="0 0 {width} {height}" role="img" aria-label="RSS over ten minutes, log scale"'
        f' data-left="{left}" data-right="{right}" data-maxt="{max_t}">',
        f'<line class="soak-axis" x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" />',
        f'<line class="soak-axis" x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" />',
    ]
    for tick in (10, 20, 50, 100, 200, 500, 1000):
        if lo <= math.log10(tick) <= hi:
            yy = y(tick)
            parts.append(f'<line class="soak-grid" x1="{left}" y1="{yy:.1f}" x2="{right}" y2="{yy:.1f}" />')
            parts.append(f'<text class="soak-label" x="{left - 10}" y="{yy + 5:.1f}" text-anchor="end">{tick}</text>')
    for tt in range(0, int(max_t) + 1, 120):
        xx = x(tt)
        parts.append(f'<text class="soak-label" x="{xx:.1f}" y="{bottom + 24}" text-anchor="middle">{tt // 60} min</text>')
    parts.append(f'<text class="soak-label" x="{left - 10}" y="16" text-anchor="end">MB</text>')
    for row in series_rows:
        fw = row["framework"]
        pts = " ".join(f"{x(p['t']):.1f},{y(p['mb']):.1f}" for p in row["series"])
        data = ";".join(f"{p['t']}:{p['mb']}" for p in row["series"])
        cls = SERIES_CLASS.get(fw, "series-other")
        hl = " is-crocket" if fw == "crocket" else ""
        parts.append(
            f'<polyline class="soak-line {cls}{hl}" fill="none" points="{pts}" data-fw="{e(fw)}" '
            f'data-name="{e(fw_name(fw))}" data-series="{data}"><title>{e(fw_name(fw))}: '
            f'{fmt_dec(row["series"][0]["mb"], 1)} to {fmt_dec(row["series"][-1]["mb"], 1)} MB</title></polyline>'
        )
        last = row["series"][-1]
        parts.append(f'<circle class="soak-end {cls}" cx="{x(last["t"]):.1f}" cy="{y(last["mb"]):.1f}" r="3.5" data-fw="{e(fw)}" />')
    parts.append(f'<line class="soak-cursor" x1="0" y1="{top}" x2="0" y2="{bottom}" />')
    parts.append('<circle class="soak-dot" cx="0" cy="0" r="5" />')
    parts.append("</svg>")
    parts.append('<div class="soak-tip" role="status" aria-live="polite" hidden></div>')
    parts.append('<ul class="soak-legend">')
    for row in series_rows:
        fw = row["framework"]
        cls = SERIES_CLASS.get(fw, "series-other")
        end = row["series"][-1]["mb"]
        parts.append(
            f'<li class="{cls}"><button type="button" class="soak-key" data-fw="{e(fw)}">'
            f'<span class="swatch" aria-hidden="true"></span>{e(fw_name(fw))}'
            f'<span class="soak-key-mb">{fmt_dec(end, 0)} MB</span></button></li>'
        )
    parts.append("</ul>")
    parts.append('<figcaption class="soak-cap">Log scale, so the small and large footprints both stay readable. Hover or tap a line, or focus a name, for exact values</figcaption>')
    parts.append("</figure>")
    return "\n".join(parts)


def contract_table(rows):
    parts = [
        '<div class="table-wrap"><table class="results prod-table">',
        "<caption>Each column is one POST body. Rejected means a 4xx. Accepted means the handler took it</caption>",
        "<thead><tr><th>Framework</th>",
    ]
    for _key, title in CONTRACT_CASES:
        parts.append(f"<th>{e(title)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in rows:
        fw = row["framework"]
        tr = ' class="is-crocket"' if fw == "crocket" else ""
        parts.append(f"<tr{tr}><th scope=\"row\">{e(fw_name(fw))}</th>")
        for key, _title in CONTRACT_CASES:
            cell = (row.get("contract") or {}).get(key) or {}
            verdict = cell.get("verdict") or "missing"
            label = cell.get("label") or "–"
            word = {
                "ok": "200",
                "rejected": "rejected",
                "accepted": "accepted",
                "failed": "failed",
                "reset": "reset",
                "error": "error",
                "missing": "–",
                "other": label,
            }.get(verdict, label)
            # For ok column, show the status; for others, the verdict word plus status.
            if key == "ok":
                text = label
            else:
                text = f"{word} · {label}" if label and label != word else word
            parts.append(f'<td class="verdict is-{e(verdict)}">{e(text)}</td>')
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "\n".join(parts)


def slow_table(rows):
    parts = [
        '<div class="table-wrap"><table class="results prod-table">',
        "<caption>Median close is how long the slow connections stayed open. Healthy traffic is GET /json while they were hanging</caption>",
        "<thead><tr><th>Framework</th><th class=\"num\">Median close</th>"
        "<th class=\"num\">Still open at 25s</th><th class=\"num\">Healthy p99</th>"
        "<th class=\"num\">Healthy errors</th></tr></thead><tbody>",
    ]
    for row in rows:
        s = row.get("slow") or {}
        fw = row["framework"]
        tr = ' class="is-crocket"' if fw == "crocket" else ""
        still = s.get("stillOpen")
        opened = s.get("opened")
        still_txt = "–" if still is None else (f"{fmt_int(still)} of {fmt_int(opened)}" if opened else fmt_int(still))
        close = "–" if s.get("medianCloseS") is None else f"{fmt_dec(s['medianCloseS'], 2)} s"
        p99 = "–" if s.get("healthyP99Ms") is None else f"{fmt_dec(s['healthyP99Ms'], 2)} ms"
        herr = "–" if s.get("healthyErr") is None else fmt_int(s["healthyErr"])
        parts.append(
            f"<tr{tr}><th scope=\"row\">{e(fw_name(fw))}</th>"
            f'<td class="num">{close}</td><td class="num">{still_txt}</td>'
            f'<td class="num">{p99}</td><td class="num">{herr}</td></tr>'
        )
    parts.append("</tbody></table></div>")
    return "\n".join(parts)


def oversize_table(rows):
    parts = [
        '<div class="table-wrap"><table class="results prod-table">',
        "<caption>A 413 is a clean rejection. A reset means the connection dropped instead</caption>",
        "<thead><tr><th>Framework</th><th>10 MB</th><th class=\"num\">Time</th>"
        "<th>100 MB</th><th class=\"num\">Time</th></tr></thead><tbody>",
    ]
    for row in rows:
        fw = row["framework"]
        tr = ' class="is-crocket"' if fw == "crocket" else ""
        o = row.get("oversize") or {}
        cells = []
        for size in ("10mb", "100mb"):
            rec = o.get(size) or {}
            verdict = rec.get("verdict") or "missing"
            label = "Connection cut" if verdict == "reset" else (rec.get("label") or "–")
            ms = rec.get("ms")
            time = "–" if ms is None else f"{fmt_dec(ms, 1)} ms"
            cells.append(f'<td class="verdict is-{e(verdict)}">{e(label)}</td><td class="num">{time}</td>')
        parts.append(f"<tr{tr}><th scope=\"row\">{e(fw_name(fw))}</th>{''.join(cells)}</tr>")
    parts.append("</tbody></table></div>")
    return "\n".join(parts)


def drain_table(rows):
    parts = [
        '<div class="table-wrap"><table class="results prod-table">',
        "<caption>In-flight is how many of the open requests still got a 200 after SIGTERM, across all runs. "
        "Exit is SIGTERM to the process gone, median of the runs</caption>",
        "<thead><tr><th>Framework</th><th class=\"num\">In-flight OK</th><th class=\"num\">Dropped</th>"
        "<th class=\"num\">Exit</th><th>How it stopped</th></tr></thead><tbody>",
    ]
    for row in rows:
        fw = row["framework"]
        tr = ' class="is-crocket"' if fw == "crocket" else ""
        ex = "–" if row.get("exitS") is None else f"{fmt_dec(row['exitS'], 2)} s"
        codes = [c for c in row.get("exitCodes") or [] if c not in ("0", "143")]
        if row["cleanRuns"] == row["runs"] and codes:
            how, cls = f"Exited on its own, but crashed in teardown (code {', '.join(codes)})", "is-other"
        elif row["cleanRuns"] == row["runs"]:
            how, cls = "Exited on its own", "is-ok"
        elif row["cleanRuns"] == 0:
            how, cls = f"Ignored SIGTERM, killed at {fmt_int(row.get('graceS') or 15)} s", "is-accepted"
        else:
            how, cls = f"Exited on its own in {row['cleanRuns']} of {row['runs']} runs", "is-other"
        parts.append(
            f"<tr{tr}><th scope=\"row\">{e(fw_name(fw))}</th>"
            f'<td class="num">{fmt_int(row["ok"])} of {fmt_int(row["total"])}</td>'
            f'<td class="num">{fmt_int(row["dropped"])}</td>'
            f'<td class="num">{ex}</td><td class="verdict {cls}">{e(how)}</td></tr>'
        )
    parts.append("</tbody></table></div>")
    return "\n".join(parts)


def churn_table(rows):
    parts = [
        '<div class="table-wrap"><table class="results prod-table">',
        "<caption>Median of three 20 second runs at an unreachable rate, with Connection: close. Range is the spread of those three</caption>",
        "<thead><tr><th>Framework</th><th class=\"num\">Max req/s</th><th class=\"num\">Range</th>"
        "<th class=\"num\">p99</th><th class=\"num\">Peak RSS</th></tr></thead><tbody>",
    ]
    for row in sorted(rows, key=lambda r: -(r.get("maxRps") or 0)):
        fw = row["framework"]
        tr = ' class="is-crocket"' if fw == "crocket" else ""
        rng = row.get("maxRpsRange") or []
        rng_txt = "–" if len(rng) != 2 else f"{fmt_int(rng[0])}–{fmt_int(rng[1])}"
        p99 = "–" if row.get("p99Ms") is None else f"{fmt_dec(row['p99Ms'], 2)} ms"
        rss = "–" if row.get("peakRssMb") is None else f"{fmt_int(row['peakRssMb'])} MB"
        parts.append(
            f"<tr{tr}><th scope=\"row\">{e(fw_name(fw))}</th>"
            f'<td class="num">{fmt_int(row.get("maxRps"))}</td>'
            f'<td class="num">{rng_txt}</td>'
            f'<td class="num">{p99}</td><td class="num">{rss}</td></tr>'
        )
    parts.append("</tbody></table></div>")
    return "\n".join(parts)


def soak_table(rows):
    parts = [
        '<div class="table-wrap"><table class="results prod-table">',
        "<caption>Sorted by how much RSS moved after the first minute, which leaves out warm-up. Held rate is what wrk2 actually achieved</caption>",
        "<thead><tr><th>Framework</th><th class=\"num\">Start RSS</th><th class=\"num\">End RSS</th>"
        "<th class=\"num\">Change</th><th class=\"num\">After 1st minute</th><th class=\"num\">Held rate</th><th class=\"num\">p99</th></tr></thead><tbody>",
    ]
    for row in rows:
        fw = row["framework"]
        tr = ' class="is-crocket"' if fw == "crocket" else ""
        start = "–" if row.get("startMb") is None else f"{fmt_int(round(row['startMb']))} MB"
        end = "–" if row.get("endMb") is None else f"{fmt_int(round(row['endMb']))} MB"
        delta = row.get("deltaMb")
        if delta is None:
            dtxt = "–"
        elif abs(delta) < 0.5:
            dtxt = "flat"
        elif delta > 0:
            dtxt = f"+{fmt_dec(delta, 1)} MB"
        else:
            dtxt = f"{fmt_dec(delta, 1)} MB"
        rate = "–" if row.get("reqS") is None else f"{fmt_int(row['reqS'])} req/s"
        p99 = "–" if row.get("p99Ms") is None else f"{fmt_dec(row['p99Ms'], 2)} ms"
        dr = row.get("driftMb")
        drift = "–" if dr is None else ("flat" if abs(dr) < 0.5 else (f"+{fmt_dec(dr, 1)} MB" if dr > 0 else f"{fmt_dec(dr, 1)} MB"))
        parts.append(
            f"<tr{tr}><th scope=\"row\">{e(fw_name(fw))}</th>"
            f'<td class="num">{start}</td><td class="num">{end}</td>'
            f'<td class="num">{dtxt}</td><td class="num">{drift}</td><td class="num">{rate}</td>'
            f'<td class="num">{p99}</td></tr>'
        )
    parts.append("</tbody></table></div>")
    return "\n".join(parts)


def _names(ids):
    return [fw_name(i) for i in ids]


def and_join(names):
    names = list(names)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + ", and " + names[-1]


def note_contract(rows):
    if not rows:
        return "I'll fill this in once every framework has a result"
    rejected_wrong = [r["framework"] for r in rows if (r.get("contract") or {}).get("wrong_type", {}).get("verdict") == "rejected"]
    accepted_wrong = [r["framework"] for r in rows if (r.get("contract") or {}).get("wrong_type", {}).get("verdict") == "accepted"]
    rejected_missing = [r["framework"] for r in rows if (r.get("contract") or {}).get("missing", {}).get("verdict") == "rejected"]
    accepted_extra = [r["framework"] for r in rows if (r.get("contract") or {}).get("extra", {}).get("verdict") == "accepted"]
    rejected_extra = [r["framework"] for r in rows if (r.get("contract") or {}).get("extra", {}).get("verdict") == "rejected"]
    malformed_rej = [r["framework"] for r in rows if (r.get("contract") or {}).get("malformed", {}).get("verdict") == "rejected"]
    bits = []
    if rejected_wrong:
        bits.append(f"{and_join(_names(rejected_wrong))} rejected a quantity with the wrong type")
    if accepted_wrong:
        bits.append(f"{and_join(_names(accepted_wrong))} took it anyway")
    if rejected_extra and not accepted_extra:
        bits.append("an extra field was rejected across the board")
    elif rejected_extra:
        bits.append(f"{and_join(_names(rejected_extra))} rejected an extra field, and {and_join(_names(accepted_extra))} ignored it")
    elif accepted_extra:
        bits.append(f"an extra field was ignored by {and_join(_names(accepted_extra))}, which is the usual default")
    if len(malformed_rej) == len(rows):
        bits.append("broken JSON was a 4xx everywhere")
    crocket = next((r for r in rows if r["framework"] == "crocket"), None)
    if crocket:
        extra_v = (crocket.get("contract") or {}).get("extra", {}).get("verdict")
        if extra_v == "accepted":
            bits.append("crocket enforces the struct, but it doesn't reject unknown fields unless you turn that on")
        elif extra_v == "rejected":
            bits.append("crocket enforced the struct on its own, with no extra package")
    if not bits:
        return "This is the contract a client actually gets, not the one in the README"
    text = bits[0].capitalize() + (", " + ", ".join(bits[1:]) if len(bits) > 1 else "")
    if not text.endswith((".", "!", "?")):
        return text
    return text.rstrip(".")


def note_slow(rows):
    if not rows:
        return "I'll fill this in once every framework has a result"
    timed = []
    hung = []
    for r in rows:
        s = r.get("slow") or {}
        close = s.get("medianCloseS")
        still = s.get("stillOpen") or 0
        if close is not None and close <= 12 and still == 0:
            timed.append(r["framework"])
        elif still and still == s.get("opened"):
            hung.append(r["framework"])
        elif close is not None and close >= 20:
            hung.append(r["framework"])
    healthy_hurt = [r["framework"] for r in rows if (r.get("slow") or {}).get("healthyErr")]
    bits = []
    if timed:
        bits.append(f"{and_join(_names(timed))} closed the slow connections around the 10 second timeout")
    if hung:
        bits.append(f"{and_join(_names(hung))} left them open for the whole 25 seconds, so there's no request timeout in play here")
    if healthy_hurt:
        bits.append(f"healthy traffic picked up errors on {and_join(_names(healthy_hurt))}")
    else:
        bits.append("the healthy requests stayed clean")
    text = bits[0].capitalize() + (", " + ", ".join(bits[1:]) if len(bits) > 1 else "")
    return text.rstrip(".")


def note_oversize(rows):
    if not rows:
        return "I'll fill this in once every framework has a result"
    clean_10 = [r["framework"] for r in rows if (r.get("oversize") or {}).get("10mb", {}).get("verdict") == "rejected"]
    reset_100 = [r["framework"] for r in rows if (r.get("oversize") or {}).get("100mb", {}).get("verdict") == "reset"]
    accepted = [r["framework"] for r in rows if (r.get("oversize") or {}).get("10mb", {}).get("verdict") == "accepted"]
    bits = []
    if len(clean_10) == len(rows):
        bits.append("everyone rejected the 10 MB post with a 4xx")
    elif clean_10:
        bits.append(f"{and_join(_names(clean_10))} rejected the 10 MB post with a 4xx")
    if accepted:
        bits.append(f"{and_join(_names(accepted))} accepted a 10 MB body, which is not what you want")
    if reset_100:
        bits.append(f"{and_join(_names(reset_100))} dropped the 100 MB connection instead of sending a clean 413")
    if not bits:
        return "This is whether the framework caps the body, and how cleanly it says no"
    text = bits[0].capitalize() + (", " + ", ".join(bits[1:]) if len(bits) > 1 else "")
    return text.rstrip(".")


def note_drain(rows):
    if not rows:
        return "I'll fill this in once every framework has a result"
    # Lower error rate and a short, bounded stop are the interesting bits.
    scored = []
    for r in rows:
        ok = r.get("ok") or 0
        err = r.get("err") or 0
        total = ok + err
        rate = (err / total) if total else None
        scored.append((r["framework"], rate, r.get("stopS"), r.get("restartS"), err, ok))
    with_rate = [s for s in scored if s[1] is not None]
    bits = []
    if with_rate:
        best = min(with_rate, key=lambda s: s[1])
        worst = max(with_rate, key=lambda s: s[1])
        if best[0] != worst[0]:
            bits.append(
                f"{fw_name(best[0])} dropped the fewest in-flight requests "
                f"({fmt_int(best[4])} errors), and {fw_name(worst[0])} dropped the most ({fmt_int(worst[4])})"
            )
        else:
            bits.append(f"{fw_name(best[0])} had {fmt_int(best[4])} errors during the cut")
    fast_restart = [s for s in scored if s[3] is not None]
    if fast_restart:
        fastest = min(fast_restart, key=lambda s: s[3])
        slowest = max(fast_restart, key=lambda s: s[3])
        if fastest[0] != slowest[0] and fastest[3] is not None and slowest[3] is not None:
            bits.append(
                f"coming back, {fw_name(fastest[0])} was ready in about {fmt_int(fastest[3])} seconds, "
                f"and {fw_name(slowest[0])} took about {fmt_int(slowest[3])}"
            )
    if not bits:
        return "A deploy shouldn't drop the requests already in flight, though some errors at the cut are hard to avoid"
    text = bits[0].capitalize() + (", " + ", ".join(bits[1:]) if len(bits) > 1 else "")
    return text.rstrip(".")


def note_churn(rows):
    if not rows:
        return "I'll fill this in once every framework has a result"
    ranked = sorted(rows, key=lambda r: -(r.get("maxRps") or 0))
    top = ranked[0]
    wide = [r for r in rows if r.get("maxRpsRange") and r["maxRpsRange"][1] >= (r["maxRpsRange"][0] or 1) * 2]
    bits = [
        f"{fw_name(top['framework'])} leads here at {fmt_int(top['maxRps'])} req/s, which is a long way down from keep-alive, as you'd expect"
    ]
    if len(ranked) > 1:
        bits.append(f"{fw_name(ranked[-1]['framework'])} is at the other end, at {fmt_int(ranked[-1]['maxRps'])}")
    if wide:
        bits.append(
            "some of these spreads are wide, so treat the median as the headline and look at the range"
        )
    text = bits[0].capitalize() + (", " + ", ".join(bits[1:]) if len(bits) > 1 else "")
    return text.rstrip(".")


def note_soak(rows):
    if not rows:
        return "I'll fill this in once every framework has a result"
    climbing = [r for r in rows if (r.get("deltaMb") or 0) >= 8]
    flat = [r for r in rows if r.get("deltaMb") is not None and abs(r["deltaMb"]) < 2]
    missed = [r for r in rows if r.get("reqS") is not None and r["reqS"] < 3500 * 0.9]
    no_wrk = [r for r in rows if r.get("reqS") is None and r.get("series")]
    bits = []
    if climbing:
        bits.append(
            f"{and_join(_names(r['framework'] for r in climbing))} climbed during the ten minutes, "
            + (
                f"about {fmt_dec(climbing[0]['deltaMb'], 0)} MB on {fw_name(climbing[0]['framework'])}"
                if len(climbing) == 1
                else "which is the kind of leak you only catch if you wait"
            )
        )
    if flat:
        bits.append(f"{and_join(_names(r['framework'] for r in flat))} stayed basically flat")
    if missed:
        bits.append(
            f"{and_join(_names(r['framework'] for r in missed))} couldn't hold 3,500 req/s, so that soak was a bit heavier than I wanted"
        )
    if no_wrk:
        bits.append("I have the RSS series even where the wrk file didn't land")
    if not bits:
        return "Ten minutes isn't a week in production, but a climb in that window is still a signal"
    text = bits[0].capitalize() + (", " + ", ".join(bits[1:]) if len(bits) > 1 else "")
    return text.rstrip(".")


# Written against the crocket and Drogon numbers we have. Generators stay as a fallback.
PROD_NOTES = {
    "drain": (
        "Rocket, hyper-express, Spring Boot, FastAPI, and crocket finished every in-flight request and exited within a second. "
        "Crocket's process then crashed on its way out, exit code 139 after the last response, which is a bug I need to fix. "
        "Axum and Fastify also answered all 16, but only because they ignored SIGTERM entirely, so the container sat there until it was SIGKILLed at 15 seconds. "
        "Drogon exited in about 70 ms and cut all 16. "
        "hyper-express needs auto_close turned off, since by default it force-closes every socket on SIGTERM, and with that default it cut all 16 too"
    ),
    "slow": (
        "Crocket and hyper-express are the only two that cut the trickling clients off, and the gap between them is just configuration. "
        "hyper-express gets uWebSockets.js's built-in idle timeout, which fired at about 8 seconds, and crocket's CROCKET_REQUEST_TIMEOUT is set to 10 seconds here. "
        "Both dropped the slow clients cleanly at their timeout, while the other six kept all 16 open for the full 25 seconds. "
        "Healthy traffic stayed clean on all eight"
    ),
    "oversize": (
        "Rocket, Drogon, and hyper-express sent a clean 413 for both sizes. "
        "Crocket sent a 413 for 10 MB and dropped the 100 MB connection. "
        "Axum and Fastify enforced their 1 MB cap by cutting the connection, so the client never saw a status. "
        "Spring Boot and FastAPI accepted both bodies with a 200, since neither app set a cap"
    ),
    "churn": (
        "Without keep-alive, throughput falls hard, as you'd expect. "
        "Most frameworks swung from a few hundred req/s to several thousand across the three runs, so I'm treating this as a first look, not a ranking. "
        "hyper-express is the odd one out. It closes the socket after the response but doesn't send Connection: close, so wrk2 kept trying to reuse each closed connection and timed out. "
        "That 6 req/s is the mismatch, not its real speed"
    ),
    "soak": (
        "All eight held 3,500 req/s for the full ten minutes with no errors. "
        "Crocket, Drogon, Rocket, Fastify, Spring Boot, and FastAPI stayed within a few megabytes. "
        "Axum climbed steadily, about 38 MB over the run, and hadn't leveled off, so it sorts last. "
        "hyper-express jumped from about 94 MB to 232 MB in the first minute, then stayed within 2 MB, which looks like warm-up rather than a leak"
    ),
    "contract": (
        "Crocket, Rocket, Axum, and FastAPI rejected a wrong type and a missing field with a 422, and ignored the extra field, which is the usual serde and Pydantic default. "
        "Fastify returned 400s, and its Ajv setup strips unknown fields instead of rejecting them. "
        "hyper-express has no schema support, so the hand-written check rejects all four. "
        "Spring Boot caught the wrong type but accepted a missing field, and Drogon accepted anything that parsed as JSON"
    ),
}

NOTES = {
    "drain": note_drain,
    "slow": note_slow,
    "oversize": note_oversize,
    "churn": note_churn,
    "soak": note_soak,
    "contract": note_contract,
}

TITLES = {
    "drain": "Graceful drain",
    "slow": "Slow clients",
    "oversize": "Oversized bodies",
    "churn": "Connection churn",
    "soak": "Memory soak",
    "contract": "Contract, on POST /echo",
}

ORDER = ["drain", "slow", "oversize", "churn", "soak", "contract"]


def render_prod_section(data):
    prod = data.get("prod") or {}
    if not any(prod.get(k) for k in ORDER):
        return ""
    parts = [
        '<section class="section" id="prod">',
        '<div class="wrap wrap-wide">',
        "<h2>Production behavior</h2>",
        '<p class="intro">Throughput is one thing. These are the tests I care about when a deploy lands, a client misbehaves, or a body is bigger than you meant to allow</p>',
    ]
    for key in ORDER:
        rows = prod.get(key) or []
        if not rows:
            continue
        parts.append(f'<article class="prod-block" id="prod-{key}">')
        parts.append(f"<h3>{e(TITLES[key])}</h3>")
        parts.append(f'<p class="test-blurb">{e(PROD_BLURBS[key])}</p>')
        if key == "churn":
            parts.append(render_simple_bars(rows, "maxRps", "req/s", "Connection-churn max requests per second"))
            parts.append(churn_table(rows))
        elif key == "soak":
            parts.append(soak_chart(rows))
            parts.append(soak_table(rows))
        elif key == "drain":
            parts.append(drain_table(rows))
        elif key == "slow":
            parts.append(slow_table(rows))
        elif key == "oversize":
            parts.append(oversize_table(rows))
        elif key == "contract":
            parts.append(contract_table(rows))
        note = PROD_NOTES.get(key) or NOTES[key](rows)
        if note:
            parts.append(f'<p class="test-note">{e(note)}</p>')
        parts.append("</article>")
    parts.append("</div></section>")
    return "\n".join(parts)
