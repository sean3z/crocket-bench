#!/usr/bin/env python3
"""Contract, slow-client, and oversized-body probes. Prints one JSON object."""
from __future__ import annotations

import argparse
import json
import socket
import statistics
import time
import urllib.error
import urllib.request
from typing import Any

def http_json(url: str, data: bytes | None = None, timeout: float = 15, headers: dict | None = None):
    hs = {"Content-Type": "application/json", "Connection": "close"}
    if headers:
        hs.update(headers)
    req = urllib.request.Request(url, data=data, headers=hs, method="POST" if data is not None else "GET")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as rs:
            body = rs.read()
            return {"status": rs.status, "body": body[:2000].decode("utf-8", "replace"), "ms": (time.perf_counter() - t0) * 1000, "error": None}
    except urllib.error.HTTPError as e:
        body = e.read() if e.fp else b""
        return {"status": e.code, "body": body[:2000].decode("utf-8", "replace"), "ms": (time.perf_counter() - t0) * 1000, "error": None}
    except Exception as e:
        return {"status": None, "body": "", "ms": (time.perf_counter() - t0) * 1000, "error": type(e).__name__ + ": " + str(e)[:200]}


def classify(case: str, res: dict) -> str:
    st, err, body = res.get("status"), res.get("error"), (res.get("body") or "")
    if err and st is None:
        return "error"
    if st in (400, 413, 415, 422, 406):
        return "rejected_clear" if body.strip() else "rejected"
    if st and 400 <= st < 500:
        return "rejected"
    if st and st >= 500:
        return "server_error"
    if st == 200:
        if case in ("ok",):
            return "ok"
        return "accepted_garbage"
    return "other"


def contract(base: str) -> dict:
    url = base.rstrip("/") + "/echo"
    cases = {
        "ok": b'{"name":"widget","qty":3}',
        "wrong_type": b'{"name":"widget","qty":"three"}',
        "missing": b'{"name":"widget"}',
        "extra": b'{"name":"widget","qty":3,"extra":true}',
        "malformed": b'{"name":"widget","qty":3',
    }
    out = {}
    for name, payload in cases.items():
        res = http_json(url, data=payload, timeout=15)
        res["class"] = classify(name, res)
        out[name] = res
    return out


def healthy_get(base: str, n: int, timeout: float = 2) -> dict:
    url = base.rstrip("/") + "/json"
    ok = 0
    err = 0
    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(url, timeout=timeout) as rs:
                rs.read()
                if rs.status == 200:
                    ok += 1
                    lat.append((time.perf_counter() - t0) * 1000)
                else:
                    err += 1
        except Exception:
            err += 1
    return {
        "ok": ok,
        "err": err,
        "p50_ms": None if not lat else round(statistics.median(lat), 2),
        "p99_ms": None if not lat else round(sorted(lat)[max(0, int(len(lat) * 0.99) - 1)], 2),
    }


def slow_clients(base: str, host: str, port: int, n_slow: int = 16, trickle_s: float = 25.0) -> dict:
    """Open connections, send incomplete headers slowly, see when the server drops them."""
    sockets = []
    opened = 0
    closed_at = []
    for i in range(n_slow):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect((host, port))
            s.settimeout(0.0)
            # Incomplete headers: never finish the request.
            s.sendall(b"GET /json HTTP/1.1\r\nHost: bench\r\nX-Slow: 1\r\n")
            sockets.append((s, time.perf_counter()))
            opened += 1
        except Exception:
            try:
                s.close()
            except Exception:
                pass
    # Meanwhile exercise healthy traffic for trickle_s seconds.
    t_end = time.perf_counter() + trickle_s
    healthy = {"ok": 0, "err": 0, "lat": []}
    last_byte = time.perf_counter()
    while time.perf_counter() < t_end:
        # trickle another header character every 2s so they stay "active"
        if time.perf_counter() - last_byte > 2:
            still = []
            for s, t0 in sockets:
                try:
                    s.sendall(b"X")
                    still.append((s, t0))
                except Exception:
                    closed_at.append(time.perf_counter() - t0)
                    try:
                        s.close()
                    except Exception:
                        pass
            sockets = still
            last_byte = time.perf_counter()
        # check for server close
        still = []
        for s, t0 in sockets:
            try:
                data = s.recv(256)
                if data == b"":
                    closed_at.append(time.perf_counter() - t0)
                    s.close()
                else:
                    still.append((s, t0))
            except BlockingIOError:
                still.append((s, t0))
            except Exception:
                closed_at.append(time.perf_counter() - t0)
                try:
                    s.close()
                except Exception:
                    pass
        sockets = still
        # one healthy request
        r = healthy_get(base, 1, timeout=2)
        healthy["ok"] += r["ok"]
        healthy["err"] += r["err"]
        if r["p50_ms"] is not None:
            healthy["lat"].append(r["p50_ms"])
        time.sleep(0.05)
    leftover = len(sockets)
    for s, t0 in sockets:
        closed_at.append(time.perf_counter() - t0)  # still open at end
        try:
            s.close()
        except Exception:
            pass
    lat = healthy["lat"]
    return {
        "opened": opened,
        "still_open_at_end": leftover,
        "close_s": [round(x, 2) for x in closed_at],
        "median_close_s": None if not closed_at else round(statistics.median(closed_at), 2),
        "healthy_ok": healthy["ok"],
        "healthy_err": healthy["err"],
        "healthy_p50_ms": None if not lat else round(statistics.median(lat), 2),
        "healthy_p99_ms": None if not lat else round(sorted(lat)[max(0, int(len(lat) * 0.99) - 1)], 2),
    }


def oversized(base: str, sizes_mb: list[int]) -> dict:
    url = base.rstrip("/") + "/echo"
    out = {}
    for mb in sizes_mb:
        # Valid-looking JSON with a huge pad field so a parser would accept it if the limit allows.
        pad = "x" * (mb * 1024 * 1024 - 40)
        payload = ('{"name":"pad","qty":1,"pad":"' + pad + '"}').encode()
        res = http_json(url, data=payload, timeout=60)
        out[f"{mb}mb"] = {
            "status": res["status"],
            "error": res["error"],
            "ms": round(res["ms"], 1),
            "body": (res["body"] or "")[:300],
            "bytes": len(payload),
        }
        del payload, pad
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--tests", default="contract,slow,oversize")
    args = ap.parse_args()
    tests = [t.strip() for t in args.tests.split(",") if t.strip()]
    result: dict[str, Any] = {"app": args.app, "base": args.base}
    if "contract" in tests:
        result["contract"] = contract(args.base)
    if "slow" in tests:
        result["slow"] = slow_clients(args.base, args.host, args.port)
    if "oversize" in tests:
        result["oversize"] = oversized(args.base, [10, 100])
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
