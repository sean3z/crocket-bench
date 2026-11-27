#!/usr/bin/env python3
"""Rebuild site/data.json and site/index.html from results/run-*.

New completed (framework, test) rows in a later run are merged in. Re-run
this script, then refresh the preview; render.js also reads data.json
directly, so updating that file alone is enough if the HTML shell already
has a panel for the test.
"""
from __future__ import annotations

import json
import re
import statistics as st
import html
from collections import defaultdict
from pathlib import Path

import prod as prodmod

SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
RESULTS = ROOT / "results"

FRAMEWORKS = {
    "crocket": {
        "id": "crocket",
        "name": "crocket",
        "version": "0.5.2",
        "language": "C++26",
        "highlight": True,
        "had": "Logger, Metrics, request timeouts, /healthz, /readyz, and x-request-id are built in",
        "added": "nothing",
    },
    "rocket": {
        "id": "rocket",
        "name": "Rocket",
        "version": "0.5.4",
        "language": "Rust",
        "highlight": False,
        "had": "There's no built-in request timeout, and no Prometheus histogram",
        "added": "rocket_prometheus, a JSON access-log fairing, a request-id fairing, health and ready routes, and a 10 second timeout around the query",
    },
    "axum": {
        "id": "axum",
        "name": "Axum",
        "version": "0.8.9",
        "language": "Rust",
        "highlight": False,
        "had": "These bits aren't built in, though official tower-http layers cover some of them",
        "added": "a Prometheus histogram, a JSON access-log middleware, tower-http's request-id and 10 second timeout layers, and health and ready routes",
    },
    "drogon": {
        "id": "drogon",
        "name": "Drogon",
        "version": "1.9.14",
        "language": "C++20",
        "highlight": False,
        "had": "A Prometheus plugin and request advice hooks are available",
        "added": "a histogram at /metrics, a JSON access log and request-id hook, health and ready routes, and a 10 second database timeout",
    },
    "fastify": {
        "id": "fastify",
        "name": "Fastify",
        "version": "5.12.8",
        "language": "Node.js 26",
        "highlight": False,
        "had": "pino logging, request-id headers, and timeouts were already there",
        "added": "prom-client for the histogram, plus health routes and a request-id hook",
    },
    "hyperexpress": {
        "id": "hyperexpress",
        "name": "hyper-express",
        "version": "7.0.2",
        "language": "Node.js 24",
        "highlight": False,
        "had": "uWebSockets.js underneath gives a body size cap and an idle-connection timeout",
        "added": "pino for the JSON access log, prom-client for the histogram, a request-id hook, health and ready routes, a hand-written check on POST /echo, a 10 second timeout around the query, and a SIGTERM drain with auto_close off",
    },
    "spring": {
        "id": "spring",
        "name": "Spring Boot",
        "version": "3.5.16",
        "language": "Java 25",
        "highlight": False,
        "had": "Structured logging is built in",
        "added": "Actuator and Micrometer for metrics and probes, plus a filter for the request id and the JSON access log",
    },
    "fastapi": {
        "id": "fastapi",
        "name": "FastAPI",
        "version": "0.165.0",
        "language": "Python 3.13",
        "highlight": False,
        "had": "These bits aren't built in",
        "added": "prometheus-fastapi-instrumentator for the histogram, asgi-correlation-id for the request id, a JSON access-log and 10 second timeout middleware, and health and ready routes",
    },
}

TESTS = [
    {
        "id": "json",
        "name": "JSON",
        "blurb": 'Each request encodes {"message":"Hello, World!"} as JSON, and that string is not cached. Same shape as the old TechEmpower JSON test',
        "pendingNote": "No results for this test yet",
    },
    {
        "id": "db",
        "name": "Single query",
        "blurb": "Each request fetches one random row from a 10,000-row World table and returns it as JSON. Same shape as the old TechEmpower single-query test",
        "pendingNote": "No results for this test yet",
    },
    {
        "id": "plaintext",
        "name": "Plaintext",
        "blurb": "Each request returns a small Hello, World! text response, which mostly stresses the HTTP pipeline. Same shape as the old TechEmpower plaintext test",
        "pendingNote": "No results for this test yet",
    },
]

COMING_IDS = []

# What stood out on the original three tests. Written after the numbers landed.
TEST_NOTES = {
    "json": "This is the HTTP and JSON encoder, no database. Crocket and Drogon stay out in front, and the gap down to Spring Boot and FastAPI is wide",
    "db": "One random row per request. Drogon does come out ahead of crocket here, though it also uses a lot more memory under that load",
    "plaintext": "The smallest response, so this is mostly the HTTP pipeline. Crocket's lead is widest here",
}

COLD_START_NOTE = (
    "Cold start is container start to the first 200 on /db. That's the number you feel when a new pod comes up during autoscaling or a deploy. "
    "Spring Boot takes about 4–5 seconds here; crocket is around 330 milliseconds"
)

FASTAPI_HELD_NOTE = (
    "FastAPI tops out around 6,000 req/s on these 4 vCPUs, so at the shared 10,000 (or 4,000 for the query test) requests just queue up. "
    "Those wouldn't be fair p50 or p99 figures, which is why the table flags them. "
    "The latency you see there is from a rate it can hold, about 60% of its max: 3,500 req/s for JSON, 3,600 for plaintext, and 2,400 for the single query. "
    "It ran four uvicorn workers, one per core"
)

CHECKLIST = [
    {
        "id": "logging",
        "short": "JSON logs",
        "name": "Structured JSON logging",
        "why": "When something breaks at 2 a.m., you want one line per request you can grep, not a pile of unstructured text",
    },
    {
        "id": "metrics",
        "short": "Metrics",
        "name": "Metrics, as a Prometheus histogram",
        "why": "Averages hide the pain. A histogram tells you if p99 just fell off a cliff",
    },
    {
        "id": "timeout",
        "short": "Timeout",
        "name": "Request timeout",
        "why": "A stuck handler shouldn't hold a connection forever, or one slow query takes the pod down with it",
    },
    {
        "id": "request_id",
        "short": "Request id",
        "name": "Request and correlation id",
        "why": "When a client says it failed, you need a shared id that shows up in their ticket and in your logs",
    },
    {
        "id": "health",
        "short": "Health",
        "name": "Health and readiness routes",
        "why": "The load balancer has to know when a pod can take traffic, and when it should stop getting any",
    },
    {
        "id": "drain",
        "short": "Drain",
        "name": "Graceful shutdown and drain",
        "why": "A deploy shouldn't drop the requests already in flight. SIGTERM should finish what's in progress, then exit",
    },
    {
        "id": "body_limit",
        "short": "Body limit",
        "name": "Body size limits",
        "why": "Without a cap, a single oversized post can run a process out of memory",
    },
    {
        "id": "validation",
        "short": "Validation",
        "name": "Input and schema validation",
        "why": "The API should reject a bad body with a clear error, not guess, and not 500",
    },
]

# (state, detail). state is built-in, added, or missing.
# Taken from apps/*/ as they ran, including a small POST /echo added later for the contract tests.
PARITY = {
    "crocket": {
        "logging": ("built-in", "Logger, one JSON line per request"),
        "metrics": ("built-in", "Metrics (/metrics)"),
        "timeout": ("built-in", "CROCKET_REQUEST_TIMEOUT, 10 s"),
        "request_id": ("built-in", "x-request-id on every response"),
        "health": ("built-in", "/healthz, /readyz (pool SELECT 1)"),
        "drain": ("built-in", "SIGTERM drains, then exits"),
        "body_limit": ("built-in", "Max body size in Config"),
        "validation": ("built-in", "Request struct is the schema"),
    },
    "rocket": {
        "logging": ("added", "tracing-subscriber JSON (fairing)"),
        "metrics": ("added", "rocket_prometheus (/metrics)"),
        "timeout": ("added", "tokio 10 s timeout, query only"),
        "request_id": ("added", "Request-id fairing"),
        "health": ("added", "/healthz, /readyz routes"),
        "drain": ("built-in", "Graceful shutdown on SIGTERM by default"),
        "body_limit": ("built-in", "Default limits, 1 MiB JSON"),
        "validation": ("built-in", "serde on the struct"),
    },
    "axum": {
        "logging": ("added", "tracing-subscriber JSON (middleware)"),
        "metrics": ("added", "metrics-exporter-prometheus (/metrics)"),
        "timeout": ("added", "tower-http TimeoutLayer, 10 s"),
        "request_id": ("added", "tower-http Set/PropagateRequestId"),
        "health": ("added", "/healthz, /readyz routes"),
        "drain": ("missing", "No with_graceful_shutdown, SIGTERM ignored"),
        "body_limit": ("added", "tower-http RequestBodyLimitLayer, 1 MB"),
        "validation": ("built-in", "serde on the struct"),
    },
    "drogon": {
        "logging": ("added", "Pre-sending advice, JSON line"),
        "metrics": ("added", "PromExporter + per-route histogram"),
        "timeout": ("added", "10 s DB client timeout, no request timeout"),
        "request_id": ("added", "Pre-routing hook"),
        "health": ("added", "/healthz, /readyz handlers"),
        "drain": ("missing", "Exits on SIGTERM, cuts in-flight"),
        "body_limit": ("added", "setClientMaxBodySize, 1 MB"),
        "validation": ("missing", "No schema, any JSON object passes"),
    },
    "fastify": {
        "logging": ("built-in", "pino"),
        "metrics": ("added", "prom-client (/metrics)"),
        "timeout": ("built-in", "requestTimeout + connectionTimeout, 10 s"),
        "request_id": ("built-in", "requestIdHeader + echo hook"),
        "health": ("added", "/healthz, /readyz routes"),
        "drain": ("missing", "No close hook, SIGTERM ignored"),
        "body_limit": ("built-in", "bodyLimit, 1 MB"),
        "validation": ("built-in", "Ajv route schema"),
    },
    "hyperexpress": {
        "logging": ("added", "pino (middleware)"),
        "metrics": ("added", "prom-client (/metrics)"),
        "timeout": ("added", "10 s query timeout, uWS idle timeout"),
        "request_id": ("added", "Request-id middleware"),
        "health": ("added", "/healthz, /readyz routes"),
        "drain": ("added", "auto_close: false + shutdown()"),
        "body_limit": ("built-in", "max_body_length, 1 MB (413)"),
        "validation": ("added", "Hand-written /echo check"),
    },
    "spring": {
        "logging": ("built-in", "Structured ECS JSON"),
        "metrics": ("added", "Actuator + Micrometer (/metrics)"),
        "timeout": ("added", "Tomcat + MVC async timeouts, 10 s query"),
        "request_id": ("added", "Request-id filter"),
        "health": ("added", "Actuator liveness + readiness"),
        "drain": ("built-in", "Graceful shutdown, the default since Boot 3.4"),
        "body_limit": ("missing", "No cap set"),
        "validation": ("missing", "Jackson only, no Bean Validation"),
    },
    "fastapi": {
        "logging": ("added", "python-json-logger (middleware)"),
        "metrics": ("added", "prometheus-fastapi-instrumentator"),
        "timeout": ("added", "asyncio 10 s middleware + asyncpg timeout"),
        "request_id": ("added", "asgi-correlation-id"),
        "health": ("added", "/healthz, /readyz routes"),
        "drain": ("built-in", "uvicorn graceful shutdown"),
        "body_limit": ("missing", "No cap by default"),
        "validation": ("built-in", "Pydantic"),
    },
}



def med(xs):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else None


def run_sort_key(path: Path):
    m = re.search(r"(\d+)$", path.name)
    return int(m.group(1)) if m else 0


def discover_runs():
    if not RESULTS.exists():
        return []
    runs = []
    for p in sorted(RESULTS.iterdir(), key=run_sort_key):
        if not (p.is_dir() and p.name.startswith("run-")):
            continue
        # A run may have more than one raw directory (later sessions win for the same framework and test).
        for raw in sorted(p.glob("raw*"), key=lambda r: (r.name != "raw", r.name)):
            if (raw / "steps.jsonl").exists() or (raw / "drain_inflight.jsonl").exists():
                runs.append(raw)
    return runs


def load_jsonl(path: Path):
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def parse_run(raw: Path):
    run_dir = raw.parent
    steps = load_jsonl(raw / "steps.jsonl")
    idle = {}
    for row in load_jsonl(raw / "idle.jsonl"):
        m = row.get("m") or {}
        idle[row["app"]] = (m.get("rss_avg_kb") or 0) / 1024
    cold = defaultdict(list)
    for row in load_jsonl(raw / "coldstart.jsonl"):
        if "cold_ms" in row:
            cold[row["app"]].append(row["cold_ms"])

    grouped = defaultdict(list)
    for row in steps:
        phase = row.get("label", "").split("_")[0]
        grouped[(row["test"], row["app"], phase)].append(row)

    results = []
    tests_seen = set()
    apps_seen = set()
    for test, app, phase in list(grouped):
        if phase != "max":
            continue
        mx = grouped[(test, app, "max")]
        cm = grouped[(test, app, "common")]
        max_rps = med([r.get("req_s") for r in mx])
        if max_rps is None:
            continue
        rps_vals = [r.get("req_s") for r in mx if r.get("req_s") is not None]
        common_rate = None
        for r in cm:
            if r.get("target_rate") is not None and r.get("req_s") is not None:
                common_rate = r["target_rate"]
                break
        peak_kb = max([r.get("rss_peak_kb") or 0 for r in mx + cm] or [0])
        errors = sum((r.get("non2xx") or 0) + (r.get("socket_errors") or 0) for r in mx + cm)
        flags = []
        common_achieved = med([r.get("req_s") for r in cm])
        if common_rate and (
            max_rps < common_rate
            or (common_achieved is not None and common_achieved < common_rate * 0.95)
        ):
            flags.append("couldnt-hold-rate")
        lg_cpu = med([r.get("lg_cpu_pct") for r in mx])
        results.append({
            "run": run_dir.name,
            "test": test,
            "framework": app,
            "maxRps": round(max_rps),
            "maxRpsRange": [round(min(rps_vals)), round(max(rps_vals))] if rps_vals else None,
            "cpuUsPerReq": round(med([r.get("cpu_us_per_req") for r in mx]) or 0),
            "appCoresAtMax": round(med([r.get("app_cpu_cores") for r in mx]) or 0, 2),
            "commonRate": common_rate,
            "p50Ms": None if med([r.get("p50_ms") for r in cm]) is None else round(med([r.get("p50_ms") for r in cm]), 2),
            "p99Ms": None if med([r.get("p99_ms") for r in cm]) is None else round(med([r.get("p99_ms") for r in cm]), 2),
            "p999Ms": None if med([r.get("p99_9_ms") for r in cm]) is None else round(med([r.get("p99_9_ms") for r in cm]), 2),
            "peakRssMb": round(peak_kb / 1024),
            "idleRssMb": None if app not in idle else round(idle[app]),
            "coldStartMs": None if not cold.get(app) else round(med(cold[app])),
            "lgCpuPct": None if lg_cpu is None else round(lg_cpu, 1),
            "errors": errors,
            "flags": flags,
        })
        tests_seen.add(test)
        apps_seen.add(app)
    return {
        "id": run_dir.name,
        "results": results,
        "tests": tests_seen,
        "frameworks": apps_seen,
    }


def merge_runs(runs):
    """Prefer the newest run for a given (framework, test)."""
    by_key = {}
    order = []
    for parsed in runs:
        order.append(parsed["id"])
        for row in parsed["results"]:
            by_key[(row["framework"], row["test"])] = row
    return order, list(by_key.values())


def collect_idle_and_held(raw_dirs):
    """Idle RSS and held-rate latencies can arrive in a later raw directory
    without replacing the max/common row from an earlier one."""
    idle = {}
    held = {}
    for raw in raw_dirs:
        for row in load_jsonl(raw / "idle.jsonl"):
            m = row.get("m") or {}
            kb = m.get("rss_avg_kb")
            if row.get("app") and kb:
                idle[row["app"]] = kb / 1024
        steps = load_jsonl(raw / "steps.jsonl")
        grouped = defaultdict(list)
        for row in steps:
            if str(row.get("label", "")).startswith("held_"):
                grouped[(row["app"], row["test"])].append(row)
        for key, rows in grouped.items():
            rate = next((r.get("target_rate") for r in rows if r.get("target_rate") is not None), None)
            held[key] = {
                "heldRate": rate,
                "heldReqS": None if med([r.get("req_s") for r in rows]) is None else round(med([r.get("req_s") for r in rows])),
                "heldP50Ms": None if med([r.get("p50_ms") for r in rows]) is None else round(med([r.get("p50_ms") for r in rows]), 2),
                "heldP99Ms": None if med([r.get("p99_ms") for r in rows]) is None else round(med([r.get("p99_ms") for r in rows]), 2),
                "heldP999Ms": None if med([r.get("p99_9_ms") for r in rows]) is None else round(med([r.get("p99_9_ms") for r in rows]), 2),
            }
    return idle, held


def apply_idle_and_held(results, idle, held):
    for row in results:
        if row["framework"] in idle:
            row["idleRssMb"] = round(idle[row["framework"]])
        extra = held.get((row["framework"], row["test"]))
        if extra:
            row.update(extra)
    return results


def fmt_int(n):
    if n is None:
        return "–"
    return f"{int(round(n)):,}"


def fmt_dec(n, digits=2):
    if n is None:
        return "–"
    return f"{n:,.{digits}f}"


def e(s):
    return html.escape("" if s is None else str(s), quote=True)


def flag_html(row):
    if "lower-bound" in (row.get("flags") or []):
        return ' <span class="flag">lower bound</span>'
    return ""


def missed_rate(row):
    return "couldnt-hold-rate" in (row.get("flags") or [])


def latency_cells(row):
    if missed_rate(row):
        if row.get("heldP50Ms") is not None and row.get("heldP99Ms") is not None:
            rate = f"{fmt_int(row.get('heldRate'))} req/s"
            return (
                f'<td class="num">{fmt_dec(row["heldP50Ms"])} ms'
                f'<span class="held-rate">at {rate}</span></td>'
                f'<td class="num">{fmt_dec(row["heldP99Ms"])} ms'
                f'<span class="held-rate">at {rate}</span></td>'
            )
        return (
            '<td class="num">–</td>'
            '<td class="num rate-miss">couldn\'t hold the rate</td>'
        )
    return (
        f'<td class="num">{fmt_dec(row["p50Ms"])} ms</td>'
        f'<td class="num">{fmt_dec(row["p99Ms"])} ms</td>'
    )


def render_bars(test, rows, frameworks):
    max_rps = max((r["maxRps"] for r in rows), default=1) or 1
    rows = sorted(rows, key=lambda r: -r["maxRps"])
    parts = [
        f'<figure class="bars"><div class="visually-hidden">{e(test["name"])}, max requests per second</div>'
    ]
    for row in rows:
        fw = frameworks[row["framework"]]
        pct = max(0.5, (row["maxRps"] / max_rps) * 100)
        cls = " is-crocket" if fw.get("highlight") else ""
        ver = f'<span class="ver">{e(fw["version"])}</span>' if fw.get("version") else ""
        parts.append(
            '<div class="bar-row">'
            f'<p class="bar-name">{e(fw["name"])}{ver}</p>'
            '<div class="bar-track">'
            '<svg class="bar-svg" viewBox="0 0 100 1" preserveAspectRatio="none" aria-hidden="true">'
            f'<rect class="bar-fill{cls}" x="0" y="0" width="{pct:.2f}" height="1"></rect>'
            "</svg></div>"
            f'<p class="bar-n">{fmt_int(row["maxRps"])}<span class="unit">req/s</span>{flag_html(row)}</p>'
            "</div>"
        )
    parts.append("</figure>")
    return "\n".join(parts)


def render_table(test, rows, frameworks):
    rows = sorted(rows, key=lambda r: -r["maxRps"])
    rate = f"{fmt_int(test.get('commonRate'))} req/s" if test.get("commonRate") else "the common rate"
    parts = [
        '<div class="table-wrap">',
        f'<table class="results" data-test="{e(test["id"])}">',
        f"<caption>Latency is at {rate}</caption>",
        "<thead><tr>",
        '<th data-sort="name" data-type="text">Framework</th>',
        '<th class="num" data-sort="maxRps" data-type="num" aria-sort="descending">Max req/s<span class="sort-ind" aria-hidden="true">↓</span></th>',
        '<th class="num" data-sort="cpuUsPerReq" data-type="num">CPU µs/req<span class="sort-ind" aria-hidden="true"></span></th>',
        '<th class="num" data-sort="p50Ms" data-type="num">p50<span class="sort-ind" aria-hidden="true"></span></th>',
        '<th class="num" data-sort="p99Ms" data-type="num">p99<span class="sort-ind" aria-hidden="true"></span></th>',
        '<th class="num" data-sort="peakRssMb" data-type="num">Peak RSS<span class="sort-ind" aria-hidden="true"></span></th>',
        '<th class="num" data-sort="idleRssMb" data-type="num">Idle RSS<span class="sort-ind" aria-hidden="true"></span></th>',
        '<th class="num" data-sort="coldStartMs" data-type="num">Cold start<span class="sort-ind" aria-hidden="true"></span></th>',
        "</tr></thead><tbody>",
    ]
    for row in rows:
        fw = frameworks[row["framework"]]
        tr_class = ' class="is-crocket"' if fw.get("highlight") else ""
        ver = ""
        if fw.get("version"):
            ver = f'<span class="ver">{e(fw["version"])} · {e(fw["language"])}</span>'
        cold = "–" if row.get("coldStartMs") is None else f"{fmt_int(row['coldStartMs'])} ms"
        idle = "–" if row.get("idleRssMb") is None else f"{fmt_int(row['idleRssMb'])} MB"
        if missed_rate(row) and row.get("heldP50Ms") is not None:
            p50_attr = row["heldP50Ms"]
            p99_attr = "" if row.get("heldP99Ms") is None else row["heldP99Ms"]
        else:
            p50_attr = "" if missed_rate(row) or row.get("p50Ms") is None else row["p50Ms"]
            p99_attr = "" if missed_rate(row) or row.get("p99Ms") is None else row["p99Ms"]
        idle_attr = "" if row.get("idleRssMb") is None else row["idleRssMb"]
        parts.append(
            f"<tr{tr_class} data-name=\"{e(fw['name'])}\" "
            f'data-max-rps="{row["maxRps"]}" data-cpu-us-per-req="{row["cpuUsPerReq"]}" '
            f'data-p50-ms="{p50_attr}" data-p99-ms="{p99_attr}" '
            f'data-peak-rss-mb="{row["peakRssMb"]}" data-idle-rss-mb="{idle_attr}" '
            f'data-cold-start-ms="{"" if row.get("coldStartMs") is None else row["coldStartMs"]}">'
            f'<th scope="row">{e(fw["name"])}{ver}</th>'
            f'<td class="num">{fmt_int(row["maxRps"])}{flag_html(row)}</td>'
            f'<td class="num">{fmt_int(row["cpuUsPerReq"])}</td>'
            f'{latency_cells(row)}'
            f'<td class="num">{fmt_int(row["peakRssMb"])} MB</td>'
            f'<td class="num">{idle}</td>'
            f'<td class="num">{cold}</td>'
            "</tr>"
        )
    parts.append("</tbody></table></div>")
    if any(r["framework"] == "fastapi" and missed_rate(r) for r in rows):
        cr = next((r for r in rows if r["framework"] == "crocket" and r.get("heldP99Ms") is not None), None)
        if cr:
            # The FastAPI held-rate explanation lives once, in the caveats. Only the per-test number is here.
            parts.append(
                f'<p class="table-note">At FastAPI\'s held rate for this test, crocket\'s p99 was around '
                f'{fmt_dec(cr["heldP99Ms"])} milliseconds</p>'
            )
    return "\n".join(parts)


def render_panel(test, rows, frameworks):
    if not rows or test.get("status") == "pending":
        return f'<p class="pending-panel">{e(test["pendingNote"])}</p>'
    stood = TEST_NOTES.get(test["id"])
    stood_html = f'\n<p class="test-note">{e(stood)}</p>' if stood else ""
    return (
        f'<p class="test-blurb">{e(test["blurb"])}</p>\n'
        f'{render_bars(test, rows, frameworks)}\n'
        f'{render_table(test, rows, frameworks)}'
        f'{stood_html}'
    )


def coming_line(coming):
    if not coming:
        return ""
    names = [c["name"] if isinstance(c, dict) else c for c in coming]
    if len(names) == 1:
        list_txt = names[0]
    else:
        list_txt = ", ".join(names[:-1]) + ", and " + names[-1]
    return (
        f'<p class="coming">{e(list_txt)} are next, and a plaintext test may join them. '
        "Those numbers will show up here when the run finishes</p>"
    )


def and_join(names):
    names = list(names)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + ", and " + names[-1]


def build_data():
    raw_dirs = discover_runs()
    parsed_runs = [parse_run(path) for path in raw_dirs]
    run_ids, results = merge_runs(parsed_runs)
    idle, held = collect_idle_and_held(raw_dirs)
    results = apply_idle_and_held(results, idle, held)
    prodmod.attach({
        "e": e,
        "fmt_int": fmt_int,
        "fmt_dec": fmt_dec,
        "med": med,
        "FRAMEWORKS": FRAMEWORKS,
        "load_jsonl": load_jsonl,
    })
    prod = prodmod.collect_prod(raw_dirs)
    present_fw = {r["framework"] for r in results}
    present_tests = {r["test"] for r in results}

    frameworks = []
    for fid, meta in FRAMEWORKS.items():
        if fid in present_fw or fid in COMING_IDS:
            frameworks.append({
                "id": fid,
                "name": meta["name"],
                "version": meta["version"],
                "language": meta["language"],
                "highlight": meta["highlight"],
                "had": meta["had"],
                "added": meta["added"],
            })
    # Unknown apps that showed up in results
    for fid in sorted(present_fw):
        if fid not in FRAMEWORKS:
            frameworks.append({
                "id": fid,
                "name": fid,
                "version": None,
                "language": None,
                "highlight": False,
                "had": None,
                "added": None,
            })

    tests = []
    for t in TESTS:
        rows = [r for r in results if r["test"] == t["id"]]
        common = next((r["commonRate"] for r in rows if r.get("commonRate")), None)
        tests.append({
            **t,
            "commonRate": common,
            "status": "complete" if rows else "pending",
            "note": TEST_NOTES.get(t["id"]),
        })
    # Any unexpected test ids from the raw data
    known = {t["id"] for t in TESTS}
    for tid in sorted(present_tests):
        if tid not in known:
            rows = [r for r in results if r["test"] == tid]
            common = next((r["commonRate"] for r in rows if r.get("commonRate")), None)
            tests.append({
                "id": tid,
                "name": tid,
                "blurb": "",
                "pendingNote": "",
                "commonRate": common,
                "status": "complete",
            })

    coming = [
        {"id": fid, "name": FRAMEWORKS[fid]["name"]}
        for fid in COMING_IDS
        if fid not in present_fw
    ]

    def row_of(fw, test):
        return next((r for r in results if r["framework"] == fw and r["test"] == test), None)

    crocket_json = row_of("crocket", "json")
    crocket_pt = row_of("crocket", "plaintext")
    crocket_db = row_of("crocket", "db")
    drogon_db = row_of("drogon", "db")
    caveats = []

    lg_bits = []
    for label, r in (("JSON", crocket_json), ("plaintext", crocket_pt)):
        if r and r.get("lgCpuPct") is not None:
            lg_bits.append(f"{label} about {r['lgCpuPct']:.0f}%")
    if lg_bits:
        caveats.append({
            "id": "loadgen",
            "text": (
                "wrk2 had a 4 vCPU droplet, same size as the app. "
                f"At crocket's max it was only around half busy ({', '.join(lg_bits)}), "
                "so I wouldn't call those numbers client-pegged, though a larger generator could still move them"
            ),
        })
    elif crocket_json or crocket_pt:
        caveats.append({
            "id": "loadgen",
            "text": (
                "The load generator was a 4 vCPU droplet, same size as the app. "
                "A larger generator could still move the fastest numbers"
            ),
        })

    if drogon_db and crocket_db:
        caveats.append({
            "id": "drogon-db",
            "text": (
                f"Drogon does beat crocket on the database test, {fmt_int(drogon_db['maxRps'])} req/s "
                f"against {fmt_int(crocket_db['maxRps'])}, and that's real. It also used about "
                f"{fmt_int(drogon_db['peakRssMb'])} MB of RSS under that load, against "
                f"{fmt_int(crocket_db['peakRssMb'])} MB for crocket"
            ),
        })

    if any("couldnt-hold-rate" in (r.get("flags") or []) and r["framework"] == "fastapi" for r in results):
        caveats.append({
            "id": "fastapi-rate",
            "text": FASTAPI_HELD_NOTE,
        })

    if any(r["framework"] == "fastify" for r in results):
        caveats.append({
            "id": "single-process",
            "text": (
                "Fastify and hyper-express each run as a single Node.js process by default, so they top out around one core. "
                "CPU per request is the fairer comparison there"
            ),
        })

    if any(r["framework"] == "hyperexpress" for r in results):
        caveats.append({
            "id": "hyperexpress-rss",
            "text": "hyper-express grew to about 480 to 780 MB of RSS at max load, though it settled around 232 MB at 3,500 req/s in the soak",
        })

    if any(r["framework"] == "spring" for r in results):
        caveats.append({
            "id": "spring-histogram",
            "text": (
                "Spring Boot needs a one-line property to expose histogram buckets at /metrics, and it's on for every number here"
            ),
        })

    lineup_ids = [fid for fid in FRAMEWORKS if fid in present_fw]
    present_meta = [FRAMEWORKS[f] for f in lineup_ids]
    lineup = and_join(
        [
            f"{m['name']} {m['version']}" if m.get("version") else m["name"]
            for m in present_meta
        ]
    ) or "the frameworks in this comparison"
    names = [m["name"] for m in present_meta]
    if names and names[0].lower() == "crocket" and len(names) > 1:
        lineup_short = f"{names[0]} against " + and_join(names[1:])
    else:
        lineup_short = and_join(names) or lineup

    unique_ids = list(dict.fromkeys(run_ids))
    data = {
        "run": {
            "id": unique_ids[0] if len(unique_ids) == 1 else "merged",
            "ids": unique_ids,
            "title": "November 2026",
            "date": "2026-11-25",
            "dateDisplay": "Nov 25, 2026",
            "track": "production",
            "lineup": lineup,
            "lineupShort": lineup_short,
        },
        "hardware": {
            "provider": "DigitalOcean",
            "size": "CPU-Optimized, 4 vCPU (c-4)",
            "region": "NYC1",
            "appCpu": "Intel Xeon Platinum 8280",
            "loadgenCpu": "Intel Xeon Platinum 8280",
            "postgres": "16.16",
            "postgresOwnDroplet": True,
            "wrk2": "-t4 -c128",
            "protocol": "HTTP/1.1 keep-alive",
            "warmup": "60 s at 20,000 req/s",
            "measured": "median of 3 × 30 s",
        },
        "repo": {
            "url": "https://github.com/sean3z/crocket-bench",
            "public": False,
        },
        "coming": coming,
        "frameworks": frameworks,
        "tests": tests,
        "results": results,
        "caveats": caveats,
        "notes": {
            "fastapiHeld": FASTAPI_HELD_NOTE,
            "coldStart": COLD_START_NOTE,
        },
        "prod": prod,
        "hardwareNote": (
            "The JSON, query, and plaintext numbers are from NYC1. "
            "The crocket and Drogon production tests ran on the same size in SFO2, because c-4 wasn't available in NYC1 that day. "
            "The other production tests, and everything for hyper-express, ran back in NYC1. The graceful drain test ran locally in Docker"
        ),
    }
    return data


ICON = {
    "built-in": '<svg class="pm-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 4.8" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    "added": '<svg class="pm-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 3.2v9.6M3.2 8h9.6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>',
    "missing": '<span class="pm-dash" aria-hidden="true">—</span>',
}
STATE_LABEL = {"built-in": "Built in", "added": "Added", "missing": "Defaulted off"}


def parity_cards(data):
    present = {r["framework"] for r in data["results"]}
    fws = [fw for fw in data["frameworks"] if fw["id"] in present and fw["id"] in PARITY]
    parts = ['<ol class="checklist">']
    for item in CHECKLIST:
        parts.append(
            "<li>"
            f"<p class=\"check-name\">{e(item['name'])}</p>"
            f"<p class=\"check-why\">{e(item['why'])}</p>"
            "</li>"
        )
    parts.append("</ol>")
    parts.append(
        '<ul class="pm-legend">'
        + "".join(f'<li class="is-{st}">{ICON[st]} {e(STATE_LABEL[st])}</li>' for st in ("built-in", "added", "missing"))
        + "</ul>"
    )
    groups = [
        ("Wired for load-test parity",
         "Added to every framework that didn't ship them, so the throughput and CPU numbers compare like with like",
         ["logging", "metrics", "timeout", "request_id", "health"]),
        ("Left at framework defaults",
         "Tested as-is, to show what's easy to miss if you don't configure it. Where an app did wire one up, it shows",
         ["drain", "body_limit", "validation"]),
    ]
    by_id = {item["id"]: item for item in CHECKLIST}
    ncol = len(fws) + 1
    parts.append('<div class="table-wrap pm-wrap"><table class="parity-matrix">')
    parts.append("<caption class=\"sr-only\">Which production features each framework had built in, needed added, or left off by default</caption>")
    parts.append("<thead><tr><th scope=\"col\">Feature</th>"
                 + "".join(f'<th scope="col" class="{"is-highlight" if fw.get("highlight") else ""}">{e(fw["name"])}</th>' for fw in fws)
                 + "</tr></thead>")
    for title, sub, ids in groups:
        parts.append(f'<tbody><tr class="pm-group"><th scope="rowgroup" colspan="{ncol}">{e(title)}'
                     f'<span class="pm-group-sub">{e(sub)}</span></th></tr>')
        for fid in ids:
            item = by_id[fid]
            cells = []
            for fw in fws:
                state, _detail = PARITY[fw["id"]][fid]
                hl = " is-highlight" if fw.get("highlight") else ""
                tip = f"{fw['name']}, {item['name']}: {STATE_LABEL[state]}"
                cells.append(f'<td class="is-{state}{hl}" title="{e(tip)}">{ICON[state]}<span class="sr-only">{e(tip)}</span></td>')
            parts.append(f'<tr><th scope="row">{e(item["name"])}</th>{"".join(cells)}</tr>')
        parts.append("</tbody>")
    parts.append("</table></div>")
    parts.append('<details class="pm-details"><summary>How each framework was wired</summary><div class="parity-grid">')
    for fw in fws:
        ver = f' {e(fw["version"])}' if fw.get("version") else ""
        lang = f'<p class="src">{e(fw["language"])}</p>' if fw.get("language") else ""
        rows = ["<ul class=\"wire-list\">"]
        for item in CHECKLIST:
            state, detail = PARITY[fw["id"]][item["id"]]
            rows.append(
                f'<li class="is-{state}"><span class="wire-icon" title="{e(STATE_LABEL[state])}">{ICON[state]}'
                f'<span class="sr-only">{e(STATE_LABEL[state])}</span></span>'
                f'<span class="wire-feat">{e(item["short"])}</span><span class="wire-detail">{e(detail)}</span></li>'
            )
        rows.append("</ul>")
        parts.append(f"<article class=\"card wire-card\"><h3>{e(fw['name'])}{ver}</h3>{lang}{''.join(rows)}</article>")
    parts.append("</div></details>")
    return "\n".join(parts)


def write_html(data):
    fw_by_id = {f["id"]: f for f in data["frameworks"]}
    # Ensure catalog entries exist for render
    for r in data["results"]:
        fw_by_id.setdefault(r["framework"], {
            "id": r["framework"], "name": r["framework"], "version": None,
            "language": None, "highlight": False,
        })

    tab_inputs = []
    tab_labels = []
    tab_panels = []
    first = True
    for test in data["tests"]:
        checked = " checked" if first else ""
        first = False
        pending = ""
        if test["status"] == "pending":
            pending = ' <span class="pending-label">not yet</span>'
        tab_inputs.append(
            f'<input class="tab-input" type="radio" name="bench-test" id="test-{e(test["id"])}"{checked}>'
        )
        tab_labels.append(
            f'<label class="tab" for="test-{e(test["id"])}">{e(test["name"])}{pending}</label>'
        )
        rows = [r for r in data["results"] if r["test"] == test["id"]]
        tab_panels.append(
            f'<section class="tab-panel" id="panel-{e(test["id"])}" role="tabpanel" '
            f'aria-labelledby="test-{e(test["id"])}">\n'
            f"{render_panel(test, rows, fw_by_id)}\n"
            "</section>"
        )

    cav = {c["id"]: c["text"] for c in data["caveats"]}
    shared = [COLD_START_NOTE]
    if "fastapi-rate" in cav:
        shared.append(cav["fastapi-rate"])
    if "hyperexpress-rss" in cav:
        shared.append(cav["hyperexpress-rss"])
    shared_notes = "".join(f'<p class="table-note">{e(t)}</p>' for t in shared)
    method_extra = ""
    for cid, title in (("loadgen", "Load generator."), ("single-process", "Single-process runtimes."), ("spring-histogram", "Spring Boot histograms.")):
        if cid in cav:
            method_extra += f"\n          <li><strong>{e(title)}</strong> {e(cav[cid])}</li>"

    repo = data["repo"]
    repo_html = (
        f'<p class="repo-note">The rig and every app\'s source will be at '
        f'<a href="{e(repo["url"])}">{e(repo["url"].removeprefix("https://"))}</a></p>'
    )

    coming = coming_line(data["coming"])
    run = data["run"]
    hw = data["hardware"]

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>crocket benchmarks</title>
  <meta name="description" content="A production-track comparison of {e(run['lineup'])} on JSON, a single database query, and plaintext, after TechEmpower's Framework Benchmarks were archived in March 2026.">
  <meta name="theme-color" content="#f4f0e6">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; img-src 'self' data:; base-uri 'self'; form-action 'self'; frame-ancestors 'none'">
  <link rel="icon" href="favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="style.css">
  <link rel="stylesheet" href="bench.css">
  <script src="theme.js"></script>
</head>
<body>
  <a class="skip" href="#content">Skip to content</a>
  <header class="site-header">
    <div class="wrap top">
      <a class="brand" href="https://crocket.dev/">
        <svg class="mark" viewBox="0 0 32 32" aria-hidden="true">
          <path class="arc" d="M6 23c5.5-11 11-12 20-6"/>
          <circle class="dot" cx="25" cy="16.2" r="2.1"/>
        </svg>
        crocket
      </a>
      <nav class="nav" aria-label="Site">
        <a href="https://crocket.dev/#features">Features</a>
        <a href="https://crocket.dev/guide/">Guide</a>
        <a href="./" aria-current="page">Benchmarks</a>
        <a href="https://github.com/sean3z/crocket/releases/tag/0.5.2">0.5.2</a>
        <a href="https://github.com/sean3z/crocket">GitHub</a>
        <button type="button" id="theme" aria-pressed="false">Dark mode</button>
      </nav>
    </div>
  </header>

  <main id="content">
    <section class="hero page-hero">
      <div class="wrap">
        <p class="kicker">{e(run["dateDisplay"])}</p>
        <h1>Benchmarks</h1>
        <p class="tagline">How crocket holds up<br>with the production bits left on</p>
        <p class="lede">TechEmpower's Framework Benchmarks were archived in March 2026, and the old result pages aren't up anymore</p>
        <p class="lede">I wanted a comparison I could point at with the boring production bits still on: a JSON access log, a request id, a Prometheus histogram, timeouts, and health routes. Here's {e(run.get("lineupShort") or run["lineup"])}, on JSON, a single database query, and plaintext</p>
        <p class="lede">Further down I also ran a graceful drain, slow clients, oversized posts, connection churn, a ten-minute memory soak, and a contract test</p>
      </div>
    </section>

    <section class="section" id="results">
      <div class="wrap wrap-wide">
        <h2>Results</h2>
        <p class="intro">Max req/s is the headline, but it isn't the whole story. CPU per request, latency at a shared rate, and memory are in the table below</p>
        <div class="tabs">
          {"".join(tab_inputs)}
          <div class="tab-bar" role="tablist" aria-label="Benchmark test">
            {"".join(tab_labels)}
          </div>
          <div class="tab-panels">
            {"".join(tab_panels)}
          </div>
          {shared_notes}
        </div>
        <div id="coming-note">{coming}</div>
      </div>
    </section>

    {prodmod.render_prod_section(data)}

    <section class="section" id="parity">
      <div class="wrap">
        <h2>The production bits people miss</h2>
        <p class="intro">These are the boring ones, and they're the reason this isn't a stripped-down "hello world" comparison. Here's why each one matters, then what was already built in and what I had to add. The wiring notes underneath have the exact packages and settings</p>
        {parity_cards(data)}
      </div>
    </section>


    <section class="section" id="methodology">
      <div class="wrap">
        <h2>How this was measured</h2>
        <p class="intro">Every framework ran on the same DigitalOcean c-4 size. {e(data.get("hardwareNote") or "")}</p>
        <ul class="method-facts">
          <li><strong>Three droplets.</strong> The app, Postgres {e(hw["postgres"].split(".")[0])}, and wrk2 each got their own 4 vCPU box. DigitalOcean assigns either an Intel Xeon Platinum 8280 or 8358 to that size, and these boxes were 8280s in both regions</li>
          <li><strong>Load.</strong> wrk2 ran <code>{e(hw["wrk2"])}</code>, {e(hw["protocol"])}. Every measured throughput run followed a 60 second warmup at 20,000 req/s</li>
          <li><strong>Max req/s.</strong> The median of three 30 second runs at an unreachable rate. That's a throughput number, not a latency one</li>
          <li><strong>Common rate.</strong> Latency is the median of three 30 second runs at a fixed rate: 10,000 req/s for JSON and plaintext, and 4,000 req/s for the single query test. Those figures are coordinated-omission corrected, so a delayed response counts as late. Where a framework couldn't hold that rate, the table shows its latency at a rate it can sustain instead, after a 60 second warmup at that same rate</li>
          <li><strong>CPU µs/req.</strong> The app's user plus system CPU, divided by requests per second, at max. That's the fairer comparison when one framework is a single process</li>
          <li><strong>Memory and start.</strong> Peak RSS is under load, idle RSS is after boot with no traffic, and cold start is container start to the first 200 on <code>/db</code></li>{method_extra}
          <li><strong>Graceful drain.</strong> This one ran locally in Docker, since it doesn't depend on cloud hardware. Every app's <code>/db</code> query hit a Postgres 16 view that sleeps 500 ms, so each request really is in flight inside the server when SIGTERM lands</li>
        </ul>
      </div>
    </section>

  </main>

  <footer class="site-footer">
    <div class="wrap">
      <p><a href="https://github.com/sean3z/crocket/blob/0.5.2/LICENSE">MIT</a> · Built with ❤️ and a little AI help</p>
    </div>
  </footer>
  <script src="render.js"></script>
</body>
</html>
"""
    (SITE / "index.html").write_text(html_out)
    print(f"wrote {SITE / 'index.html'}")


def main():
    data = build_data()
    (SITE / "data.json").write_text(json.dumps(data, indent=2) + "\n")
    print(f"wrote {SITE / 'data.json'} ({len(data['results'])} results)")
    write_html(data)


if __name__ == "__main__":
    main()
