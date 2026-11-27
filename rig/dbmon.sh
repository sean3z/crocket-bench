#!/usr/bin/env bash
# usage: dbmon.sh SECONDS -> host cpu util on db droplet
read b0 t0 < <(awk '/^cpu /{print $2+$3+$4+$6+$7+$8, $2+$3+$4+$5+$6+$7+$8}' /proc/stat); sleep $1
read b1 t1 < <(awk '/^cpu /{print $2+$3+$4+$6+$7+$8, $2+$3+$4+$5+$6+$7+$8}' /proc/stat)
python3 -c "print('{\"db_cpu_pct\":%.1f}' % (100*($b1-$b0)/max(1,$t1-$t0)))"
