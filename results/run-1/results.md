# crocket-bench run 1: production track

wrk2 -t4 -c128, HTTP/1.1 keep-alive. Max = median of 3 × 30 s at an unreachable rate after a 60 s warmup at 20k req/s. Latency = median of 3 × 30 s at a common fixed rate (coordinated-omission corrected). CPU µs/req = app user+sys CPU ÷ req/s. Errors = non-2xx + socket errors summed over the 6 measured runs.

## db

| Framework | Max req/s (median) | range | CPU µs/req at max | App cores at max | Postgres CPU % | Common rate | p50 ms | p99 ms | p99.9 ms | Peak RSS MB | Idle RSS MB | Errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| drogon | 49,232 | 49,188–50,868 | 75 | 3.71 | 59 | 4,000 | 1.27 | 2.27 | 2.57 | 201 | 0 | 0 |
| axum | 39,882 | 39,839–40,089 | 84 | 3.35 | 64 | 4,000 | 1.27 | 2.30 | 2.61 | 29 | 9 | 0 |
| rocket | 30,524 | 30,375–30,854 | 111 | 3.40 | 46 | 4,000 | 1.31 | 2.34 | 2.61 | 21 | 11 | 0 |
| spring | 10,567 | 10,465–10,668 | 311 | 3.27 | 16 | 4,000 | 1.46 | 2.69 | 4.07 | 449 | 240 | 0 |
| fastapi | 3,930 | 3,930–3,936 | 944 | 3.71 | 17 | 4,000 | 321.71 | 3240.00 | 3485.00 | 270 | 261 | 0 |

## json

| Framework | Max req/s (median) | range | CPU µs/req at max | App cores at max | Postgres CPU % | Common rate | p50 ms | p99 ms | p99.9 ms | Peak RSS MB | Idle RSS MB | Errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| crocket | 90,800 | 87,528–98,631 | 35 | 3.18 | 0 | 10,000 | 1.04 | 1.97 | 2.22 | 34 | 15 | 0 |
| drogon | 79,919 | 79,890–80,437 | 42 | 3.37 | 0 | 10,000 | 1.03 | 2.00 | 2.35 | 18 | 0 | 0 |
| axum | 72,061 | 71,778–72,126 | 42 | 3.03 | 0 | 10,000 | 1.05 | 1.99 | 2.31 | 31 | 9 | 0 |
| rocket | 55,230 | 53,517–56,641 | 60 | 3.33 | 0 | 10,000 | 1.07 | 2.01 | 2.34 | 16 | 11 | 0 |
| spring | 14,027 | 13,985–14,288 | 231 | 3.23 | 0 | 10,000 | 1.32 | 3.07 | 5.19 | 446 | 240 | 0 |
| fastapi | 5,909 | 5,826–5,922 | 616 | 3.62 | 0 | 10,000 | 7100.00 | 15340.00 | 15660.00 | 267 | 261 | 0 |

## plaintext

| Framework | Max req/s (median) | range | CPU µs/req at max | App cores at max | Postgres CPU % | Common rate | p50 ms | p99 ms | p99.9 ms | Peak RSS MB | Idle RSS MB | Errors |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| crocket | 102,479 | 97,521–108,194 | 31 | 3.15 | 0 | 10,000 | 1.05 | 2.01 | 2.24 | 32 | 15 | 0 |
| drogon | 90,471 | 89,800–90,731 | 35 | 3.21 | 0 | 10,000 | 1.02 | 1.99 | 2.27 | 201 | 0 | 0 |
| axum | 72,800 | 70,695–73,820 | 42 | 3.05 | 0 | 10,000 | 1.06 | 2.00 | 2.31 | 33 | 9 | 0 |
| rocket | 57,136 | 53,255–58,307 | 58 | 3.32 | 0 | 10,000 | 1.06 | 2.02 | 2.34 | 20 | 11 | 0 |
| fastify | 23,394 | 21,337–24,118 | 44 | 1.04 | 0 | 10,000 | 1.23 | 2.47 | 3.05 | 389 | 84 | 0 |
| spring | 16,781 | 16,720–16,842 | 192 | 3.22 | 0 | 10,000 | 1.59 | 4.13 | 5.86 | 430 | 240 | 0 |
| fastapi | 6,082 | 6,076–6,098 | 592 | 3.60 | 0 | 10,000 | 7170.00 | 13510.00 | 13740.00 | 270 | 261 | 0 |

## Cold start (container start to first 200 on /db)

- rocket: 204 ms (n=1)
- axum: 293 ms (n=1)
- drogon: 219 ms (n=1)
- crocket: 335 ms (n=1)
- fastapi: 1120 ms (n=1)
- fastify: 405 ms (n=1)
- spring: 4709 ms (n=1)
