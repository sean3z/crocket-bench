#!/usr/bin/env bash
# rss_watch.sh PID SECONDS INTERVAL -> jsonl {t,rss_kb} on stdout
PID=$1; N=$2; I=${3:-5}
if ! [ "$PID" -gt 0 ] 2>/dev/null; then echo "need PID" >&2; exit 2; fi
pids(){ echo $PID $(pgrep -P $PID); }
rss(){ t=0; for p in $(pids); do r=$(awk '/VmRSS/{print $2}' /proc/$p/status 2>/dev/null); t=$((t+${r:-0})); done; echo $t; }
start=$(date +%s)
end=$((start + N))
while [ $(date +%s) -le $end ]; do
  printf '{"t":%s,"rss_kb":%s}\n' $(( $(date +%s) - start )) "$(rss)"
  sleep $I
done
