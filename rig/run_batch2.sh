#!/usr/bin/env bash
# Batch 2 on one fresh 3-droplet nyc1 c-4 rig (app, Postgres 16, loadgen), one lifecycle.
#   1. Production tests only: axum rocket fastify spring fastapi
#   2. hyperexpress: standard suite (idle, cold start, plaintext/json/db max + common [+ held]) + production tests
# Hang-proof: every remote wrk2/python/docker step is wrapped in `timeout -k 10 ...` on the remote
# AND a box-side `timeout` on the ssh (TMO). A sub-step that times out is logged and the batch moves on.
# ALWAYS tears down via teardown.sh.real (EXIT trap) and kills the watchdog when 0 droplets remain.
set -u
R=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$R"
. "$R/lib.sh"
export RAW=${RAW:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/results/run-1/raw-v2}
mkdir -p "$RAW"
REGION=${REGION:-nyc1}
PROD_APPS="axum rocket fastify spring fastapi"
NEW=hyperexpress
APPS_BUILD="$NEW $PROD_APPS"
declare -A COMMON=([json]=10000 [db]=4000 [plaintext]=10000)
declare -A HELD=([json]=3500 [db]=2400 [plaintext]=3600)

# ssh: keepalives + connect timeout; box-side hard cap per call via TMO (default 300 s)
SSHO="-i $R/cb_key -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 -o BatchMode=yes"
s(){ local h=$1; shift; timeout -k 10 ${TMO:-300} ssh $SSHO root@$h "$@"; }
# run a named sub-step; log rc (124/137 = timed out) but never abort the batch
sub(){ local name=$1; shift; "$@"; local rc=$?; [ $rc = 0 ] || log "WARN $name rc=$rc$([ $rc = 124 -o $rc = 137 ] && echo ' (TIMEOUT)')"; return 0; }

cleanup(){ log "teardown"; bash $R/teardown.sh.real >> $RAW/teardown.txt 2>&1; tail -2 $RAW/teardown.txt | tee -a $RAW/progress.log
  n=$(doctl compute droplet list --tag-name crocket-bench --format ID --no-header | wc -l)
  if [ "$n" = 0 ]; then [ -f $R/watchdog.pid ] && kill $(cat $R/watchdog.pid) 2>/dev/null; pkill -f "rig/watchdog.sh"; log "0 droplets; watchdog killed"; else log "droplets left=$n; watchdog kept"; fi
  log "batch2 exit"; }
trap cleanup EXIT
trap 'log "signal; exiting"; exit 130' INT TERM HUP
doctl account get >/dev/null || { log "doctl not authenticated; nothing created"; trap - EXIT; exit 2; }
if ! pgrep -f "rig/watchdog.sh" >/dev/null; then export DIGITALOCEAN_ACCESS_TOKEN; ( nohup setsid bash $R/watchdog.sh 14400 > $R/watchdog.log 2>&1 < /dev/null & echo $! > $R/watchdog.pid ); fi
sleep 3; log "watchdog pid $(cat $R/watchdog.pid) armed"
grep -q AUTH_OK $R/watchdog.log || { log "watchdog cannot authenticate; aborting before creating droplets"; exit 3; }

[ -f cb_key ] || ssh-keygen -q -t ed25519 -N '' -f cb_key -C crocket-bench
KEY=$(doctl compute ssh-key import crocket-bench --public-key-file cb_key.pub --format ID --no-header 2>/dev/null || doctl compute ssh-key list --format ID,Name --no-header | awk '$2=="crocket-bench"{print $1}')
date +%s > $RAW/start_ts
log "creating 3x c-4 in $REGION"
for r in srv db lg; do
  timeout 600 doctl compute droplet create cb-$r --size c-4 --image ubuntu-24-04-x64 --region $REGION --ssh-keys $KEY --tag-name crocket-bench --user-data-file ud_batch2.sh --wait --no-header --format ID >/dev/null & done; wait
doctl compute droplet list --tag-name crocket-bench --format Name,PublicIPv4,PrivateIPv4,Status --no-header > droplets
cat droplets >> $RAW/progress.log; load
[ -n "$SRV" ] && [ -n "$DB" ] && [ -n "$LG" ] || { log "droplet create failed"; exit 1; }
for i in $(seq 60); do n=0; for h in $SRV $DB $LG; do TMO=20 s $h 'test -f /root/ready' 2>/dev/null && n=$((n+1)); done; [ $n = 3 ] && break; sleep 10; done
log "droplets ready ($n/3)"; [ $n = 3 ] || exit 1

tar czf /tmp/cbapps.tgz -C $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd) apps
timeout 120 scp $SSHO /tmp/cbapps.tgz srv_start.sh conformance.sh mon.sh rss_watch.sh root@$SRV:/root/ && TMO=120 s $SRV 'tar xzf /root/cbapps.tgz -C /root'
timeout 120 scp $SSHO dbmon.sh ../db/init.sql root@$DB:/root/
timeout 120 scp $SSHO dbmon.sh prod_tests.py drain_load.py root@$LG:/root/
TMO=300 s $DB 'until docker exec pg pg_isready -U bench >/dev/null 2>&1; do sleep 1; done; docker exec -i pg psql -q -U bench bench < /root/init.sql; docker exec pg psql -tAU bench bench -c "select count(*) from world"' >> $RAW/progress.log
TMO=60 s $SRV 'uname -r; lscpu | grep "Model name"; nproc; free -m | head -2; docker version --format "docker {{.Server.Version}}"' > $RAW/server_env.txt
TMO=60 s $LG 'lscpu | grep "Model name"; nproc' > $RAW/lg_env.txt; TMO=60 s $DB 'docker exec pg postgres --version; nproc' > $RAW/db_env.txt
log "building: $APPS_BUILD"
build1(){ TMO=2400 s $SRV "cd /root/apps/$1 && timeout 2300 docker build -t bench-$1 . > /root/build_$1.log 2>&1; echo $1 build exit \$? at \$(date +%T)" >> $RAW/progress.log; }
# lane 1: hyperexpress, fastify, fastapi (quick) then spring; lane 2: the two Rust builds
( for a in $NEW fastify fastapi spring; do build1 $a; done ) & ( for a in axum rocket; do build1 $a; done ) & wait
for a in $APPS_BUILD; do TMO=60 s $SRV "tail -30 /root/build_$a.log" > $RAW/build_$a.log 2>&1; done
TMO=60 s $SRV "for a in $APPS_BUILD; do docker image inspect bench-\$a --format \"\$a {{.Id}}\" 2>/dev/null; done" > $RAW/images.txt
log "builds finished: $(awk '{print $1}' $RAW/images.txt | tr '\n' ' ')"

has_image(){ grep -q "^$1 " $RAW/images.txt; }
apppid(){ TMO=30 s $SRV "docker inspect -f '{{.State.Pid}}' app" 2>/dev/null | tr -d '[:space:]'; }

start_app(){
  local a=$1
  TMO=60 s $SRV "docker rm -f app >/dev/null 2>&1; fuser -k 8000/tcp >/dev/null 2>&1; sleep 1; true"
  if ! TMO=180 s $SRV "timeout -k 5 150 bash /root/srv_start.sh $a $DB_P" > /tmp/cbc 2>>$RAW/start_err_$a.txt; then
    log "$a failed to start"; cat /tmp/cbc >> $RAW/coldstart.jsonl; return 1; fi
  cat /tmp/cbc >> $RAW/coldstart.jsonl; return 0
}

# monitors: app (mon.sh), db + lg CPU (dbmon.sh); each capped
mon_start(){ local pid=$1 mdur=$2
  ( sleep 3; TMO=$((mdur+60)) s $SRV "timeout -k 5 $((mdur+30)) bash /root/mon.sh $pid $mdur" > /tmp/cbmon.$$ ) &
  ( sleep 3; TMO=$((mdur+60)) s $DB "timeout -k 5 $((mdur+30)) bash /root/dbmon.sh $mdur" > /tmp/cbdbm.$$ ) &
  ( sleep 3; TMO=$((mdur+60)) s $LG "timeout -k 5 $((mdur+30)) bash /root/dbmon.sh $mdur" | sed 's/db_cpu_pct/lg_cpu_pct/' > /tmp/cblgm.$$ ) & }

# step APP TEST LABEL RATE SECONDS (standard load step, wrk2 hard-capped at dur+45)
step(){ local app=$1 test=$2 label=$3 rate=$4 dur=$5 pid hp; hp=$(health_path $app)
  pid=$(apppid); rm -f /tmp/cbmon.$$ /tmp/cbdbm.$$ /tmp/cblgm.$$
  [[ $pid =~ ^[0-9]+$ ]] || { log "$app $test $label: no app pid, skipped"; return 0; }
  mon_start $pid $((dur-4))
  TMO=$((dur+90)) s $LG "( for i in \$(seq $((dur/5))); do curl -s -m 3 -o /dev/null http://$SRV_P:8000$hp; [ \$((i%3)) = 0 ] && curl -s -m 3 -o /dev/null http://$SRV_P:8000/metrics; sleep 5; done ) & \
         timeout -k 10 $((dur+45))s wrk2 -t4 -c128 -d${dur}s -R$rate --timeout 10s --latency http://$SRV_P:8000/$test; echo wrk_rc=\$?; wait" > $RAW/wrk_${app}_${test}_${label}.txt 2>&1
  local rc=$?; [ $rc = 0 ] || log "WARN $app $test $label ssh rc=$rc"
  grep -q 'wrk_rc=124' $RAW/wrk_${app}_${test}_${label}.txt && log "WARN $app $test $label wrk2 TIMEOUT"
  wait
  python3 $R/parse_step.py "$app" "$test" "$label" "$rate" $RAW/wrk_${app}_${test}_${label}.txt /tmp/cbmon.$$ /tmp/cbdbm.$$ /tmp/cblgm.$$ >> $RAW/steps.jsonl || log "WARN parse $app $test $label"
}

churn_step(){
  local app=$1 label=$2 rate=$3 dur=$4 pid
  pid=$(apppid); rm -f /tmp/cbmon.$$ /tmp/cbdbm.$$ /tmp/cblgm.$$
  [[ $pid =~ ^[0-9]+$ ]] || { log "$app churn $label: no app pid, skipped"; return 0; }
  mon_start $pid $((dur-4))
  TMO=$((dur+90)) s $LG "timeout -k 10 $((dur+45))s wrk2 -t4 -c128 -d${dur}s -R$rate --timeout 10s --latency -H 'Connection: close' http://$SRV_P:8000/json; echo wrk_rc=\$?" \
    > $RAW/wrk_${app}_json_${label}.txt 2>&1
  local rc=$?; [ $rc = 0 ] || log "WARN $app churn $label ssh rc=$rc"
  grep -q 'wrk_rc=124' $RAW/wrk_${app}_json_${label}.txt && log "WARN $app churn $label wrk2 TIMEOUT"
  wait
  python3 $R/parse_step.py "$app" "json" "$label" "$rate" $RAW/wrk_${app}_json_${label}.txt /tmp/cbmon.$$ /tmp/cbdbm.$$ /tmp/cblgm.$$ >> $RAW/steps.jsonl || log "WARN parse $app churn $label"
  # let TIME_WAIT sockets on the loadgen drain between churn reps
  sleep 5
}

# 10-minute soak: wrk2 under nohup + timeout on the loadgen; box polls the pid with a hard 12-minute cap.
soak_json(){
  local a=$1 pid
  pid=$(apppid); [[ $pid =~ ^[0-9]+$ ]] || { log "$a soak: no app pid, skipped"; return 0; }
  log "$a soak 600s at 3500"
  TMO=60 s $SRV "rm -f /root/soak_rss.jsonl; nohup timeout -k 5 660 bash /root/rss_watch.sh $pid 600 5 > /root/soak_rss.jsonl 2>/dev/null < /dev/null &"
  TMO=60 s $LG "rm -f /root/wrk_soak.txt /root/wrk_soak.pid; nohup timeout -k 10 645s wrk2 -t4 -c128 -d600s -R3500 --timeout 10s --latency http://$SRV_P:8000/json > /root/wrk_soak.txt 2>&1 < /dev/null & echo \$! > /root/wrk_soak.pid"
  sleep 590
  for i in $(seq 13); do TMO=30 s $LG "kill -0 \$(cat /root/wrk_soak.pid) 2>/dev/null" || break; sleep 10; done
  TMO=60 s $LG "[ -f /root/wrk_soak.pid ] && kill -9 \$(cat /root/wrk_soak.pid) 2>/dev/null; true"
  timeout 120 scp $SSHO root@$LG:/root/wrk_soak.txt $RAW/wrk_${a}_json_soak.txt >/dev/null || log "WARN $a soak wrk fetch"
  timeout 120 scp $SSHO root@$SRV:/root/soak_rss.jsonl $RAW/soak_rss_$a.jsonl >/dev/null || log "WARN $a soak rss fetch"
}

run_drain(){
  local a=$1
  log "$a drain"
  TMO=120 s $LG "timeout -k 5 90 python3 /root/drain_load.py --url http://$SRV_P:8000/json --seconds 40" > $RAW/drain_load_$a.json 2>$RAW/drain_load_$a.err &
  local loadpid=$!
  sleep 8
  date +%s > /tmp/drain_term_$a
  TMO=90 s $SRV "docker kill --signal=TERM app >/dev/null 2>&1; for i in \$(seq 30); do docker inspect -f '{{.State.Running}}' app 2>/dev/null | grep -q false && break; ss -ltn | grep -q ':8000' || break; sleep 1; done; docker rm -f app >/dev/null 2>&1; true" \
    > $RAW/drain_stop_$a.txt 2>&1 || log "WARN $a drain stop"
  date +%s > /tmp/drain_down_$a
  start_app $a > $RAW/drain_restart_$a.txt 2>&1 || log "WARN $a drain restart"
  date +%s > /tmp/drain_up_$a
  wait $loadpid 2>/dev/null || log "WARN $a drain load rc!=0"
  python3 - <<PY >> $RAW/drain_$a.json
import json, pathlib
def n(p):
    try: return int(pathlib.Path(p).read_text().strip())
    except Exception: return None
load=pathlib.Path("$RAW/drain_load_$a.json")
obj={"app":"$a","sigterm_unix":n("/tmp/drain_term_$a"),"down_unix":n("/tmp/drain_down_$a"),"up_unix":n("/tmp/drain_up_$a")}
if load.exists() and load.stat().st_size:
    try: obj["load"]=json.loads(load.read_text())
    except Exception as e: obj["load_error"]=str(e)
print(json.dumps(obj))
PY
}

run_probes(){
  local a=$1 pid
  TMO=420 s $LG "timeout -k 10 360 python3 /root/prod_tests.py --app $a --base http://$SRV_P:8000 --host $SRV_P --port 8000 --tests contract,slow,oversize" \
    > $RAW/probes_$a.json 2>$RAW/probes_$a.err || log "WARN $a probes rc!=0"
  pid=$(apppid)
  [[ $pid =~ ^[0-9]+$ ]] && TMO=60 s $SRV "bash /root/mon.sh $pid 2" > $RAW/rss_after_oversize_$a.txt 2>/dev/null
  return 0
}

prod_suite(){
  local a=$1
  TMO=120 s $SRV "timeout -k 5 100 bash /root/conformance.sh $a" > $RAW/conformance_$a.txt 2>&1; log "$a conformance exit=$?"
  sub "$a probes" run_probes $a
  # probes can leave the app wedged/dead (slow clients, oversize); restart if it no longer answers
  TMO=30 s $SRV "curl -sf -m 5 -o /dev/null http://127.0.0.1:8000/json" || { log "$a not answering after probes; restarting"; start_app $a || return 1; }
  log "$a churn"
  churn_step $a churn_warmup 1000000 30
  for r in 1 2 3; do churn_step $a churn_max_r$r 1000000 20; done
  TMO=30 s $SRV "curl -sf -m 5 -o /dev/null http://127.0.0.1:8000/json" || { log "$a not answering after churn; restarting"; start_app $a || return 1; }
  sub "$a soak" soak_json $a
  sub "$a drain" run_drain $a
}

med_common(){ python3 - "$RAW/steps.jsonl" "$1" "$2" <<'PY'
import json,sys,statistics
rows=[json.loads(l) for l in open(sys.argv[1]) if l.strip()]
v=[r["req_s"] for r in rows if r.get("app")==sys.argv[2] and r.get("test")==sys.argv[3] and str(r.get("label","")).startswith("common_") and r.get("req_s") is not None]
print(int(statistics.median(v)) if v else 0)
PY
}

# ---------- 2a. hyperexpress standard suite ----------
if has_image $NEW; then
  a=$NEW; log "$a standard start"
  if start_app $a; then
    sub "$a cold2" start_app $a; sub "$a cold3" start_app $a   # 3 cold-start samples
    idle_sample $a || true
    for t in plaintext json db; do
      step $a $t warmup 20000 60
      for r in 1 2 3; do step $a $t max_r$r 1000000 30; done
      for r in 1 2 3; do step $a $t common_r$r ${COMMON[$t]} 30; done
      got=$(med_common $a $t); log "$a $t common median ${got} of ${COMMON[$t]}"
      if [ "${got:-0}" -lt $(( ${COMMON[$t]} * 95 / 100 )) ]; then
        log "$a $t could not hold common rate; held at ${HELD[$t]}"
        for r in 1 2 3; do step $a $t held_r$r ${HELD[$t]} 30; done
      fi
      log "$a $t done"
    done
    TMO=30 s $SRV "docker logs --tail 3 app" > $RAW/applog_tail_$a.txt 2>&1
    log "$a prod tests start"; prod_suite $a; log "$a prod tests done"
  fi
else log "$NEW image missing; skipped"; fi

# ---------- 1. remaining five: production tests ----------
for a in $PROD_APPS; do
  has_image $a || { log "$a image missing; skipped"; continue; }
  log "$a prod tests start"
  if start_app $a; then prod_suite $a; else log "$a skip prod tests"; fi
  log "$a prod tests done"
done
log "batch2 done"
[ -f $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/batch2_hx.sh ] && . $(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/batch2_hx.sh
