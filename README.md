# crocket-bench

Production-defaults-on web framework benchmarks behind https://crocket.dev/benchmarks/

Eight frameworks, each with structured JSON logs, a Prometheus histogram, a request timeout, a request id, and health routes turned on, so the numbers compare apps you'd actually ship: crocket, Drogon, Axum, Rocket, Fastify, hyper-express, Spring Boot, and FastAPI

## Layout

- `apps/` one app per framework, same endpoints: `/plaintext`, `/json`, `/db`, `POST /echo`, `/healthz`, `/readyz`, `/metrics`
- `db/init.sql` the 10,000-row World table
- `rig/` the DigitalOcean harness (three c-4 droplets: app, Postgres 16, wrk2), plus `rig/local/` for the in-flight drain test that runs in local Docker
- `results/run-1/` raw wrk2 output, monitors, probes, and summaries
- `site/` the generator for the results page (`python3 site/build.py`)

## Running it

The cloud runs need `doctl` authenticated with `DIGITALOCEAN_ACCESS_TOKEN` in the environment. Every runner tears its droplets down on exit, and `rig/watchdog.sh` deletes anything tagged `crocket-bench` after a hard cap

    bash rig/run_tier.sh "crocket:json,db,plaintext axum:json"   # standard suite
    bash rig/run_batch2.sh                                       # production tests

The drain test only needs Docker:

    bash rig/local/pg_slow.sh          # Postgres 16 with a 500 ms World view, prints its IP
    python3 rig/local/drain_inflight.py --dbh <that IP>

## License

MIT
