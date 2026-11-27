#!/usr/bin/env bash
# Local Postgres 16 for the in-flight drain test: World table behind a view that sleeps 500 ms per query.
set -e
sudo docker rm -f pgdrain >/dev/null 2>&1 || true
sudo docker run -d --name pgdrain -e POSTGRES_USER=bench -e POSTGRES_PASSWORD=bench -e POSTGRES_DB=bench \
  postgres:16 -c max_connections=200 >/dev/null
until sudo docker exec pgdrain pg_isready -U bench >/dev/null 2>&1; do sleep 1; done; sleep 2
sudo docker exec -i pgdrain psql -q -U bench bench < "$(dirname "$0")/../../db/init.sql"
sudo docker exec -i pgdrain psql -q -U bench bench <<'SQL'
alter table world rename to world_t;
create view world as
  select w.id, w.randomnumber from world_t w cross join lateral (select pg_sleep(0.5)) s;
SQL
sudo docker exec pgdrain psql -tAU bench bench -c "explain analyze select id, randomnumber from world where id = 42" | tail -2
sudo docker inspect -f "{{.NetworkSettings.IPAddress}}" pgdrain
