#!/usr/bin/env bash
# On cb-srv: production-track checks for APP. Prints PASS/FAIL lines; exit 1 on any FAIL.
APP=$1; U=http://127.0.0.1:8000; f=0
ck(){ if eval "$2" >/dev/null 2>&1; then echo "PASS $1"; else echo "FAIL $1"; f=1; fi; }
HP=/readyz; LP=/healthz; [ $APP = spring ] && { HP=/health/readiness; LP=/health/liveness; }
ck "plaintext body"       "[ \"\$(curl -s $U/plaintext)\" = \"Hello, World!\" ]"
ck "plaintext content-type" "curl -s -D - -o /dev/null $U/plaintext | grep -qi '^content-type: text/plain'"
ck "json body"            "curl -s $U/json | jq -e '.message==\"Hello, World!\" and (keys|length)==1'"
ck "json content-type"    "curl -s -D - -o /dev/null $U/json | grep -qi '^content-type: application/json'"
ck "db shape"             "curl -s $U/db | jq -e '(.id|type)==\"number\" and (.randomNumber|type)==\"number\" and .id>=1 and .id<=10000'"
ck "db random ids"        "[ \$(for i in \$(seq 20); do curl -s $U/db | jq .id; done | sort -u | wc -l) -gt 10 ]"
ck "x-request-id echoed"  "curl -s -D - -o /dev/null -H 'x-request-id: abc123' $U/json | grep -qi '^x-request-id: abc123'"
ck "x-request-id generated" "curl -s -D - -o /dev/null $U/json | grep -qi '^x-request-id: .'"
ck "liveness $LP"         "curl -sf $U$LP"
ck "readiness $HP"        "curl -sf $U$HP"
ck "metrics histogram"    "curl -s $U/metrics | grep -q '_bucket'"
ck "json access log line" "curl -s $U/json -H 'x-request-id: logcheck42' && sleep 1 && ${LOGCMD:-docker logs --tail 50 app 2>&1} | grep logcheck42 | tail -1 | jq -e ."
exit $f
