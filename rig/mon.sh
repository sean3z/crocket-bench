#!/usr/bin/env bash
# usage: mon.sh PID SECONDS -> JSON: app cpu cores (user/sys, proc+children), peak/avg RSS, host cpu util, docker daemon cpu
PID=$1; N=$2; HZ=$(getconf CLK_TCK)
if [ -z "${PID:-}" ] || [ -z "${N:-}" ] || ! [ "$PID" -gt 0 ] 2>/dev/null || ! [ "$N" -gt 0 ] 2>/dev/null; then
  echo "mon.sh: need PID and SECONDS as positive integers" >&2
  exit 2
fi
pids(){ echo $PID $(pgrep -P $PID); }
ticks(){ u=0; k=0; for p in $(pids); do read -r a b < <(awk '{print $14, $15}' /proc/$p/stat 2>/dev/null); u=$((u+${a:-0})); k=$((k+${b:-0})); done; echo $u $k; }
rss(){ t=0; for p in $(pids); do r=$(awk '/VmRSS/{print $2}' /proc/$p/status 2>/dev/null); t=$((t+${r:-0})); done; echo $t; }
host(){ awk '/^cpu /{print $2+$3+$4+$6+$7+$8, $2+$3+$4+$5+$6+$7+$8}' /proc/stat; }
dk(){ t=0; for p in $(pgrep -f 'dockerd|containerd-shim'); do read -r a b < <(awk '{print $14, $15}' /proc/$p/stat); t=$((t+a+b)); done; echo $t; }
read u0 k0 < <(ticks); read h0b h0t < <(host); d0=$(dk); max=0; sum=0
for i in $(seq $N); do sleep 1; r=$(rss); sum=$((sum+r)); [ $r -gt $max ] && max=$r; done
read u1 k1 < <(ticks); read h1b h1t < <(host); d1=$(dk)
python3 -c "print('{\"app_cpu_cores\":%.3f,\"app_user_cores\":%.3f,\"app_sys_cores\":%.3f,\"rss_peak_kb\":%d,\"rss_avg_kb\":%d,\"host_cpu_pct\":%.1f,\"docker_cpu_cores\":%.2f}' % (($u1-$u0+$k1-$k0)/$HZ/$N, ($u1-$u0)/$HZ/$N, ($k1-$k0)/$HZ/$N, $max, $sum//$N, 100*($h1b-$h0b)/max(1,$h1t-$h0t), ($d1-$d0)/$HZ/$N))"
