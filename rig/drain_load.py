#!/usr/bin/env python3
"""Steady GET load. Writes one JSON summary to stdout when --seconds elapses."""
import argparse, json, time, urllib.request
from collections import Counter

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--seconds", type=float, required=True)
    ap.add_argument("--conns", type=int, default=32)
    args = ap.parse_args()
    t_end = time.perf_counter() + args.seconds
    ok = err = 0
    by = Counter()
    # Single-threaded is enough to see drops during a restart; keep it simple and honest.
    while time.perf_counter() < t_end:
        try:
            with urllib.request.urlopen(args.url, timeout=2) as rs:
                rs.read()
                by[rs.status] += 1
                if rs.status == 200:
                    ok += 1
                else:
                    err += 1
        except Exception as e:
            err += 1
            by[type(e).__name__] += 1
    print(json.dumps({"ok": ok, "err": err, "by": dict(by), "seconds": args.seconds}))

if __name__ == "__main__":
    main()
