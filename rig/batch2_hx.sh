# Sourced at the end of run_batch2.sh (same shell, same functions/droplets) when the
# hyperexpress image was rebuilt mid-run after the bookworm/glibc build failure.
# soak_json override: in the main run the nohup ssh calls stayed attached for the whole 600 s soak,
# so the fixed `sleep 590` afterwards only added idle time. Poll from the start instead (hard 12-min cap).
soak_json(){
  local a=$1 pid
  pid=$(apppid); [[ $pid =~ ^[0-9]+$ ]] || { log "$a soak: no app pid, skipped"; return 0; }
  log "$a soak 600s at 3500"
  TMO=60 s $SRV "rm -f /root/soak_rss.jsonl; nohup timeout -k 5 660 bash /root/rss_watch.sh $pid 600 5 > /root/soak_rss.jsonl 2>/dev/null < /dev/null &" &
  TMO=60 s $LG "rm -f /root/wrk_soak.txt /root/wrk_soak.pid; nohup timeout -k 10 645s wrk2 -t4 -c128 -d600s -R3500 --timeout 10s --latency http://$SRV_P:8000/json > /root/wrk_soak.txt 2>&1 < /dev/null & echo \$! > /root/wrk_soak.pid" &
  sleep 30
  for i in $(seq 66); do TMO=30 s $LG "kill -0 \$(cat /root/wrk_soak.pid) 2>/dev/null" || break; sleep 10; done
  TMO=60 s $LG "[ -f /root/wrk_soak.pid ] && kill -9 \$(cat /root/wrk_soak.pid) 2>/dev/null; true"
  sleep 5
  timeout 120 scp $SSHO root@$LG:/root/wrk_soak.txt $RAW/wrk_${a}_json_soak.txt >/dev/null || log "WARN $a soak wrk fetch"
  timeout 120 scp $SSHO root@$SRV:/root/soak_rss.jsonl $RAW/soak_rss_$a.jsonl >/dev/null || log "WARN $a soak rss fetch"
  wait
}
TMO=60 s $SRV "docker image inspect bench-$NEW --format \"$NEW {{.Id}}\"" >> $RAW/images.txt 2>/dev/null
TMO=60 s $SRV "tail -30 /root/build_$NEW.log" > $RAW/build_$NEW.log 2>&1
if has_image $NEW; then
  a=$NEW; log "$a standard start (after rebuild)"
  if start_app $a; then
    sub "$a cold2" start_app $a; sub "$a cold3" start_app $a
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
else log "$NEW image still missing; skipped"; fi
log "batch2 hx done"

# Retry pass: any production probe or churn rep that came back empty (ssh rc=255 blips).
for a in $PROD_APPS $NEW; do
  has_image $a || continue
  need=""
  [ -s $RAW/probes_$a.json ] || need+="probes "
  for l in churn_max_r1 churn_max_r2 churn_max_r3; do grep -q "Requests/sec" $RAW/wrk_${a}_json_$l.txt 2>/dev/null || need+="$l "; done
  [ -n "$need" ] || continue
  log "$a retry: $need"
  start_app $a || { log "$a retry start failed"; continue; }
  for n in $need; do
    case $n in
      probes) sub "$a probes retry" run_probes $a ;;
      churn_*) churn_step $a $n 1000000 20 ;;
    esac
  done
done
log "retry pass done"
