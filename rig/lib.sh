R=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SSHO="-i $R/cb_key -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
s(){ h=$1; shift; timeout ${TMO:-900} ssh $SSHO root@$h "$@"; }
ip(){ awk -v n=$1 -v c=$2 '$1==n{print $c}' $R/droplets; }
load(){ SRV=$(ip cb-srv 2); DB=$(ip cb-db 2); LG=$(ip cb-lg 2); SRV_P=$(ip cb-srv 3); DB_P=$(ip cb-db 3); }
log(){ echo "$(date +%T) $*" | tee -a $RAW/progress.log; }
health_path(){ case $1 in spring) echo /health/readiness;; *) echo /readyz;; esac; }
# step APP TEST LABEL RATE SECONDS -> one JSON line (wrk2 + app mon + db mon); probes+scrapes run during it
step(){ local app=$1 test=$2 label=$3 rate=$4 dur=$5 pid mdur=$(( $5-4 )) hp; hp=$(health_path $app)
  pid=$(s $SRV "docker inspect -f '{{.State.Pid}}' app")
  ( sleep 3; s $SRV "bash /root/mon.sh $pid $mdur" > /tmp/cbmon.$$ ) &
  ( sleep 3; s $DB "bash /root/dbmon.sh $mdur" > /tmp/cbdbm.$$ ) &
  ( sleep 3; s $LG "bash /root/dbmon.sh $mdur" | sed 's/db_cpu_pct/lg_cpu_pct/' > /tmp/cblgm.$$ ) &
  s $LG "( for i in \$(seq $((dur/5))); do curl -s -o /dev/null http://$SRV_P:8000$hp; [ \$((i%3)) = 0 ] && curl -s -o /dev/null http://$SRV_P:8000/metrics; sleep 5; done ) & \
         wrk2 -t4 -c128 -d${dur}s -R$rate --timeout 10s --latency http://$SRV_P:8000/$test; wait" > $RAW/wrk_${app}_${test}_${label}.txt 2>&1
  wait
  python3 $R/parse_step.py "$app" "$test" "$label" "$rate" $RAW/wrk_${app}_${test}_${label}.txt /tmp/cbmon.$$ /tmp/cbdbm.$$ /tmp/cblgm.$$
}

# idle_sample APP -> append one idle.jsonl row. Retries, because a dropped SSH
# used to leave PID empty and turn `mon.sh $pid 5` into `mon.sh 5` (N unset).
idle_sample(){
  local a=$1 attempt mon pid
  for attempt in 1 2 3; do
    sleep 5
    pid=$(s $SRV "docker inspect -f '{{.State.Pid}}' app" | tr -d '[:space:]')
    if ! [[ $pid =~ ^[0-9]+$ ]]; then log "$a idle: no pid (attempt $attempt)"; continue; fi
    mon=$(s $SRV "bash /root/mon.sh $pid 5") || true
    if echo "$mon" | grep -q rss_avg_kb; then
      printf '%s\n' "$mon" | sed "s/^/{\"app\":\"$a\",\"phase\":\"idle\",\"m\":/; s/$/}/" >> $RAW/idle.jsonl
      log "$a idle rss ok (attempt $attempt)"
      return 0
    fi
    log "$a idle mon failed (attempt $attempt)"
  done
  log "$a idle sample missing after retries"
  return 1
}
