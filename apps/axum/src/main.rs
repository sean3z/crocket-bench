// TFB plaintext/json/db on Axum 0.8 + tokio-postgres/deadpool, production track.
// Added for parity: metrics + metrics-exporter-prometheus (/metrics histogram), tracing-subscriber JSON +
// non_blocking writer (one line per request from a middleware), tower-http SetRequestId/PropagateRequestId,
// tower-http TimeoutLayer (10s) + statement_timeout, RequestBodyLimitLayer, /healthz + /readyz routes.
use axum::{body::Body, extract::{MatchedPath, State}, http::{HeaderName, Request, StatusCode}, middleware::{self, Next},
    response::{IntoResponse, Response}, routing::{get, post}, Json, Router};
use deadpool_postgres::{Manager, ManagerConfig, Pool, RecyclingMethod};
use metrics_exporter_prometheus::{Matcher, PrometheusBuilder, PrometheusHandle};
use rand::Rng;
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};
use tower_http::request_id::{MakeRequestId, PropagateRequestIdLayer, RequestId, SetRequestIdLayer};

#[derive(Serialize)]
struct Message { message: &'static str }
#[derive(Serialize, Deserialize)]
struct Item { name: String, qty: i32 }
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct World { id: i32, random_number: i32 }

#[derive(Clone)]
struct App { pool: Pool, prom: PrometheusHandle }

async fn plaintext() -> &'static str { "Hello, World!" }
async fn json() -> Json<Message> { Json(Message { message: "Hello, World!" }) }
async fn echo(Json(item): Json<Item>) -> Json<Item> { Json(item) }

async fn db(State(app): State<App>) -> Result<Json<World>, StatusCode> {
    let id: i32 = rand::thread_rng().gen_range(1..=10000);
    let c = app.pool.get().await.map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
    let st = c.prepare_cached("select id, randomnumber from world where id = $1").await.map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    let row = c.query_one(&st, &[&id]).await.map_err(|_| StatusCode::INTERNAL_SERVER_ERROR)?;
    Ok(Json(World { id: row.get(0), random_number: row.get(1) }))
}

async fn healthz() -> &'static str { "ok" }
async fn readyz(State(app): State<App>) -> StatusCode {
    match app.pool.get().await { Ok(c) if c.simple_query("select 1").await.is_ok() => StatusCode::OK, _ => StatusCode::SERVICE_UNAVAILABLE }
}
async fn metrics_route(State(app): State<App>) -> String { app.prom.render() }

#[derive(Clone)]
struct Uuid4;
impl MakeRequestId for Uuid4 {
    fn make_request_id<B>(&mut self, _: &Request<B>) -> Option<RequestId> {
        uuid::Uuid::new_v4().simple().to_string().parse().ok().map(RequestId::new)
    }
}

async fn observe(req: Request<Body>, next: Next) -> Response {
    let t0 = Instant::now();
    let method = req.method().clone();
    let path = req.uri().path().to_owned();
    let route = req.extensions().get::<MatchedPath>().map(|m| m.as_str().to_owned()).unwrap_or_default();
    let rid = req.headers().get("x-request-id").and_then(|v| v.to_str().ok()).unwrap_or("").to_owned();
    let res = next.run(req).await;
    let secs = t0.elapsed().as_secs_f64();
    let status = res.status().as_u16();
    metrics::histogram!("http_request_duration_seconds", "method" => method.to_string(), "route" => route.clone(), "status" => status.to_string()).record(secs);
    tracing::info!(method = %method, route = %route, path = %path, status, duration_ms = secs * 1e3, request_id = %rid, "request");
    res
}

#[tokio::main]
async fn main() {
    let (writer, _guard) = tracing_appender::non_blocking(std::io::stdout());
    tracing_subscriber::fmt().json().with_env_filter("bench=info").with_writer(writer).with_target(false).init();
    let prom = PrometheusBuilder::new()
        .set_buckets_for_metric(Matcher::Full("http_request_duration_seconds".into()),
            &[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]).unwrap()
        .install_recorder().unwrap();
    let url = std::env::var("DATABASE_URL").unwrap_or("postgresql://bench:bench@localhost/bench".into());
    let mut cfg: tokio_postgres::Config = url.parse().unwrap();
    cfg.options("-c statement_timeout=10000");
    let mgr = Manager::from_config(cfg, tokio_postgres::NoTls, ManagerConfig { recycling_method: RecyclingMethod::Fast });
    let pool = Pool::builder(mgr).max_size(16).build().unwrap();
    { let mut v = Vec::new(); for _ in 0..16 { v.push(pool.get().await.expect("db")); } }  // warm: min = max
    let x_request_id = HeaderName::from_static("x-request-id");
    let app = Router::new()
        .route("/plaintext", get(plaintext)).route("/json", get(json)).route("/echo", post(echo)).route("/db", get(db))
        .route("/healthz", get(healthz)).route("/readyz", get(readyz)).route("/metrics", get(metrics_route))
        .route_layer(middleware::from_fn(observe))
        .layer(tower_http::timeout::TimeoutLayer::new(Duration::from_secs(10)))
        .layer(tower_http::limit::RequestBodyLimitLayer::new(1 << 20))
        .layer(PropagateRequestIdLayer::new(x_request_id.clone()))
        .layer(SetRequestIdLayer::new(x_request_id, Uuid4))
        .with_state(App { pool, prom });
    let l = tokio::net::TcpListener::bind("0.0.0.0:8000").await.unwrap();
    axum::serve(l, app.into_make_service()).await.unwrap();
    let _ = IntoResponse::into_response(());
}
