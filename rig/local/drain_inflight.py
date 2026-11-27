#!/usr/bin/env python3
"""Real in-flight graceful drain test, run locally in Docker (no cloud hardware needed).

Every app's GET /db runs the same query against a `world` VIEW that wraps the table with
pg_sleep(0.5), so each request genuinely sits inside the server (waiting on Postgres) for
~500 ms. Identical for all frameworks; no app code changes.

Per run: start the app, warm its pool, open N concurrent GET /db requests, send SIGTERM
~150 ms later, send ZERO new requests, then record how many in-flight requests got a 200,
how long the process took to exit, and whether it exited on its own (or was SIGKILLed after
the grace period).
"""
import argparse, json, subprocess, threading, time, http.client, statistics, sys, os

RIG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = ["sudo", "docker"]

def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)

def get_db(timeout):
    t0 = time.perf_counter()
    try:
        c = http.client.HTTPConnection("127.0.0.1", 8000, timeout=timeout)
        c.request("GET", "/db", headers={"Connection": "close"})
        r = c.getresponse(); r.read(); c.close()
        return {"status": r.status, "ms": (time.perf_counter() - t0) * 1000, "error": None}
    except Exception as e:
        return {"status": None, "ms": (time.perf_counter() - t0) * 1000, "error": type(e).__name__}

def burst(n, timeout=20):
    out = [None] * n
    def w(i): out[i] = get_db(timeout)
    ts = [threading.Thread(target=w, args=(i,)) for i in range(n)]
    for t in ts: t.start()
    return ts, out

def running():
    r = sh(D + ["inspect", "-f", "{{.State.Running}}", "app"])
    return r.stdout.strip() == "true"

def one_run(app, n, delay_s, grace_s, dbh):
    sh(D + ["rm", "-f", "app"])
    st = sh(["sudo", "bash", os.path.join(RIG, "srv_start.sh"), app, dbh], timeout=180)
    if st.returncode != 0:
        return {"error": "start failed", "detail": (st.stdout + st.stderr)[-300:]}
    for _ in range(3):                      # warm the pool: every connection used at least once
        ts, _o = burst(n); [t.join() for t in ts]
    ts, out = burst(n)
    time.sleep(delay_s)
    t_term = time.perf_counter()
    sh(D + ["kill", "--signal=TERM", "app"])
    exited, exit_s = False, None
    while time.perf_counter() - t_term < grace_s:
        if not running():
            exited, exit_s = True, time.perf_counter() - t_term; break
        time.sleep(0.02)
    if not exited:
        sh(D + ["kill", "--signal=KILL", "app"])
        exit_s = time.perf_counter() - t_term
    for t in ts: t.join()
    code = sh(D + ["inspect", "-f", "{{.State.ExitCode}}", "app"]).stdout.strip()
    logs = sh(D + ["logs", "--tail", "5", "app"]).stdout[-600:]
    sh(D + ["rm", "-f", "app"])
    ok = sum(1 for r in out if r["status"] == 200)
    return {"inFlightOk": ok, "inFlightDropped": n - ok, "exitS": round(exit_s, 3),
            "cleanExit": exited, "exitCode": code,
            "errors": sorted({(r["error"] or str(r["status"])) for r in out if r["status"] != 200}),
            "okMs": [round(r["ms"]) for r in out if r["status"] == 200]}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apps", default="crocket rocket axum drogon fastify hyperexpress spring fastapi")
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--delay", type=float, default=0.15)
    ap.add_argument("--grace", type=float, default=15.0)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--dbh", required=True, help="Postgres host (the pgdrain container IP)")
    a = ap.parse_args()
    for app in a.apps.split():
        for r in range(1, a.runs + 1):
            res = one_run(app, a.n, a.delay, a.grace, a.dbh)
            res.update({"app": app, "run": r, "n": a.n, "termAfterMs": int(a.delay * 1000), "graceS": a.grace})
            print(json.dumps(res), flush=True)

if __name__ == "__main__":
    main()
