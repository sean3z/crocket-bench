import json, re, sys
app, test, label, rate, w, *mons = sys.argv[1:]
r = {"app": app, "test": test, "label": label, "target_rate": int(rate)}
txt = open(w).read()
def ms(v, u): return float(v) * {"us": 1e-3, "ms": 1, "s": 1e3, "m": 6e4}[u]
for p in ("50.000", "90.000", "99.000", "99.900"):
    mm = re.search(r"^\s*%s%%\s+([\d.]+)(us|ms|s|m)\b" % re.escape(p), txt, re.M)
    if mm: r["p" + p.split(".")[0] + ("_9" if p == "99.900" else "") + "_ms"] = round(ms(*mm.groups()), 3)
mm = re.search(r"Requests/sec:\s+([\d.]+)", txt); r["req_s"] = float(mm.group(1)) if mm else None
mm = re.search(r"(\d+) requests in", txt); r["requests"] = int(mm.group(1)) if mm else None
mm = re.search(r"Non-2xx or 3xx responses:\s+(\d+)", txt); r["non2xx"] = int(mm.group(1)) if mm else 0
mm = re.search(r"Socket errors: connect (\d+), read (\d+), write (\d+), timeout (\d+)", txt)
r["socket_errors"] = sum(map(int, mm.groups())) if mm else 0
for f in mons:
    try: r.update(json.loads(open(f).read()))
    except Exception as e: r.setdefault("mon_err", str(e))
if r.get("req_s") and r.get("app_cpu_cores"): r["cpu_us_per_req"] = round(r["app_cpu_cores"] * 1e6 / r["req_s"], 1)
print(json.dumps(r))
