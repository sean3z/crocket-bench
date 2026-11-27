#!/usr/bin/env bash
# On cb-srv: start APP against DB host; print cold start + startup RSS JSON.
APP=$1; DBH=$2; docker rm -f app >/dev/null 2>&1
case $APP in
  crocket) ENVS="-e CROCKET_DATABASE_URL=postgresql://bench:bench@$DBH/bench";;
  fastify) ENVS="-e DATABASE_URL=postgresql://bench:bench@$DBH/bench";;
  spring)  ENVS="-e DATABASE_JDBC=jdbc:postgresql://$DBH/bench";;
  rocket)  ENVS="-e ROCKET_DATABASES={bench={url=\"postgresql://bench:bench@$DBH/bench\",max_connections=16,min_connections=16}}";;
  axum|fastapi|hyperexpress) ENVS="-e DATABASE_URL=postgresql://bench:bench@$DBH/bench";;
  drogon)  ENVS="-e DB_HOST=$DBH";;
esac
T0=$(date +%s%N)
docker run -d --name app --network host --log-opt max-size=200m --log-opt max-file=2 $ENVS bench-$APP >/dev/null
until curl -sf -o /dev/null http://127.0.0.1:8000/db; do sleep 0.005; [ $(( ($(date +%s%N)-T0)/1000000000 )) -gt 120 ] && { echo "{\"app\":\"$APP\",\"error\":\"no 200 in 120s\"}"; docker logs app 2>&1 | tail -20 >&2; exit 1; }; done
T1=$(date +%s%N); PID=$(docker inspect -f '{{.State.Pid}}' app)
RSS=0; for p in $PID $(pgrep -P $PID); do r=$(awk '/VmRSS/{print $2}' /proc/$p/status 2>/dev/null); RSS=$((RSS+${r:-0})); done
echo "{\"app\":\"$APP\",\"cold_ms\":$(( (T1-T0)/1000000 )),\"startup_rss_kb\":$RSS,\"pid\":$PID}"
