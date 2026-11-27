#!/usr/bin/env bash
# usage: rig/run_tier.sh "app:test,test app:test ..." [lg_size]   e.g. "rocket:json,db crocket:plaintext"
# (legacy form: rig/run_tier.sh "crocket fastify spring" "json db" [lg_size])
# Creates 3 tagged droplets (app c-4, db c-4, lg c-8), runs, collects, and ALWAYS tears down.
set -u
R=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ $1 == *:* ]]; then PLAN=$1; LGS=${2:-c-4}; else PLAN=""; for a in $1; do PLAN+="$a:${2// /,} "; done; LGS=${3:-c-4}; fi
APPS=$(for e in $PLAN; do echo ${e%%:*}; done | awk '!seen[$0]++' | tr '\n' ' ')
export RAW=${RAW:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/results/run-1/raw}; mkdir -p $RAW; cd $R; . $R/lib.sh
declare -A COMMON=([json]=10000 [db]=4000 [plaintext]=10000)
cleanup(){ log "teardown"; bash $R/teardown.sh >> $RAW/teardown.txt 2>&1; tail -2 $RAW/teardown.txt | tee -a $RAW/progress.log
  n=$(doctl compute droplet list --tag-name crocket-bench --format ID --no-header | wc -l)
  if [ "$n" = 0 ] && [ -f $R/watchdog.pid ]; then kill $(cat $R/watchdog.pid) 2>/dev/null; pkill -f "rig/watchdog.sh" ; log "0 droplets; watchdog killed"; else log "droplets left=$n; watchdog kept"; fi; }
trap cleanup EXIT
doctl account get >/dev/null || { log "doctl not authenticated; nothing created"; trap - EXIT; exit 2; }
# Box-side watchdog (detached, survives this script): delete all crocket-bench droplets after 3 h.
if ! pgrep -f "rig/watchdog.sh" >/dev/null; then export DIGITALOCEAN_ACCESS_TOKEN; ( nohup setsid bash $R/watchdog.sh 10800 > $R/watchdog.log 2>&1 < /dev/null & echo $! > $R/watchdog.pid ); fi
sleep 3; log "watchdog pid $(cat $R/watchdog.pid): $(head -1 $R/watchdog.log)"
grep -q AUTH_OK $R/watchdog.log || { log "watchdog cannot authenticate; aborting before creating droplets"; exit 3; }
if [ "${REUSE:-0}" != 1 ]; then
[ -f cb_key ] || ssh-keygen -q -t ed25519 -N '' -f cb_key -C crocket-bench
KEY=$(doctl compute ssh-key import crocket-bench --public-key-file cb_key.pub --format ID --no-header 2>/dev/null || doctl compute ssh-key list --format ID,Name --no-header | awk '$2=="crocket-bench"{print $1}')
date +%s > $RAW/start_ts_$(date +%H%M)
for r in srv db lg; do sz=c-4; [ $r = lg ] && sz=$LGS
  doctl compute droplet create cb-$r --size $sz --image ubuntu-24-04-x64 --region nyc1 --ssh-keys $KEY --tag-name crocket-bench --user-data-file ud_common.sh --wait --no-header --format ID >/dev/null & done; wait
doctl compute droplet list --tag-name crocket-bench --format Name,PublicIPv4,PrivateIPv4,Status --no-header > droplets; fi; cat droplets >> $RAW/progress.log; load
for i in $(seq 60); do n=0; for h in $SRV $DB $LG; do TMO=20 s $h 'test -f /root/ready' 2>/dev/null && n=$((n+1)); done; [ $n = 3 ] && break; sleep 10; done
log "droplets ready ($n/3)"; [ $n = 3 ] || exit 1
tar czf /tmp/cbapps.tgz -C $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd) apps
scp $SSHO /tmp/cbapps.tgz srv_start.sh conformance.sh mon.sh root@$SRV:/root/ && s $SRV 'tar xzf /root/cbapps.tgz -C /root'
scp $SSHO dbmon.sh ../db/init.sql root@$DB:/root/; scp $SSHO dbmon.sh root@$LG:/root/
# ssh reaper (not a child, so `wait` never blocks on it): kills step ssh sessions that outlive 120 s
( nohup setsid bash $R/ssh_reaper.sh > /dev/null 2>&1 < /dev/null & )
s $DB 'until docker exec pg pg_isready -U bench >/dev/null 2>&1; do sleep 1; done; docker exec -i pg psql -q -U bench bench < /root/init.sql; docker exec pg psql -tAU bench bench -c "select count(*) from world"' >> $RAW/progress.log
log "building: $APPS"
build1(){ TMO=2400 s $SRV "docker image inspect bench-$1 >/dev/null 2>&1 && [ -z \"\${FORCE_BUILD:-}\" ] && { echo $1 image exists; exit 0; }; cd /root/apps/$1 && docker build -t bench-$1 . > /root/build_$1.log 2>&1; echo $1 build exit \$? at \$(date +%T)" >> $RAW/progress.log; }
L1=""; L2=""; i=0; for a in $APPS; do if [ $((i%2)) = 0 ]; then L1+="$a "; else L2+="$a "; fi; i=$((i+1)); done
( for a in $L1; do build1 $a; done ) & ( for a in $L2; do build1 $a; done ) & wait
for a in $APPS; do s $SRV "tail -30 /root/build_$a.log" > $RAW/build_$a.log 2>&1; done
s $SRV 'uname -r; lscpu | grep "Model name"; nproc; free -m | head -2; docker version --format "docker {{.Server.Version}}"' > $RAW/server_env.txt
s $LG 'lscpu | grep "Model name"; nproc' > $RAW/lg_env.txt; s $DB 'docker exec pg postgres --version; nproc' > $RAW/db_env.txt
for e in $PLAN; do a=${e%%:*}; TESTS=${e#*:}; TESTS=${TESTS//,/ }
  log "$a start ($TESTS)"
  if ! s $SRV "bash /root/srv_start.sh $a $DB_P" > /tmp/cbc 2>>$RAW/start_err_$a.txt; then log "$a failed to start"; cat /tmp/cbc >> $RAW/coldstart.jsonl; continue; fi
  cat /tmp/cbc >> $RAW/coldstart.jsonl
  s $SRV "bash /root/conformance.sh $a" > $RAW/conformance_$a.txt 2>&1; log "$a conformance exit=$?"
  s $SRV "docker image inspect bench-$a --format '{{.Id}}'" | sed "s/^/$a /" >> $RAW/images.txt
  idle_sample $a
  for t in $TESTS; do
    step $a $t warmup 20000 60 >> $RAW/steps.jsonl
    for r in 1 2 3; do step $a $t max_r$r 1000000 30 >> $RAW/steps.jsonl; done
    for r in 1 2 3; do step $a $t common_r$r ${COMMON[$t]} 30 >> $RAW/steps.jsonl; done
    log "$a $t done"
  done
  s $SRV "docker logs --tail 3 app" > $RAW/applog_tail_$a.txt 2>&1
done
python3 $R/report.py > $(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/results/run-1/results.md
log "tier done"
