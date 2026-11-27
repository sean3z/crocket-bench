// TFB plaintext/json/db on Rocket 0.5 + rocket_db_pools (deadpool_postgres; the sqlx_postgres backend pins sqlx 0.7, which no longer compiles on current rustc), production track.
// Added for parity: rocket_prometheus (/metrics histogram), tracing-subscriber JSON + non_blocking writer
// via a fairing (one line per request), a request-id fairing, /healthz + /readyz routes, and a
// tokio::time::timeout(10s) around the query (Rocket has no request timeout).
#[macro_use]
extern crate rocket;

use rand::Rng;
use rocket::fairing::{Fairing, Info, Kind};
use rocket::http::{Header, Status};
use rocket::serde::json::Json;
use rocket::{Data, Request, Response};
use rocket_db_pools::{deadpool_postgres, Connection, Database};
use rocket_prometheus::PrometheusMetrics;
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};

#[derive(Database)]
#[database("bench")]
struct Db(deadpool_postgres::Pool);

#[derive(Serialize)]
struct Message { message: &'static str }
#[derive(Serialize, Deserialize)]
struct Item { name: String, qty: i32 }

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct World { id: i32, random_number: i32 }

const TIMEOUT: Duration = Duration::from_secs(10);

#[get("/plaintext")]
fn plaintext() -> &'static str { "Hello, World!" }

#[get("/json")]
fn json() -> Json<Message> { Json(Message { message: "Hello, World!" }) }

#[post("/echo", data = "<item>")]
fn echo(item: Json<Item>) -> Json<Item> { item }

#[get("/db")]
async fn db(mut db: Connection<Db>) -> Result<Json<World>, Status> {
    let id: i32 = rand::thread_rng().gen_range(1..=10000);
    let q = async {
        let st = db.prepare_cached("select id, randomnumber from world where id = $1").await?;
        let row = db.query_one(&st, &[&id]).await?;
        Ok::<_, rocket_db_pools::deadpool_postgres::tokio_postgres::Error>(World { id: row.get(0), random_number: row.get(1) })
    };
    match tokio::time::timeout(TIMEOUT, q).await {
        Ok(Ok(w)) => Ok(Json(w)),
        Ok(Err(_)) => Err(Status::InternalServerError),
        Err(_) => Err(Status::ServiceUnavailable),
    }
}

#[get("/healthz")]
fn healthz() -> &'static str { "ok" }

#[get("/readyz")]
async fn readyz(mut db: Connection<Db>) -> Status {
    match db.simple_query("select 1").await { Ok(_) => Status::Ok, Err(_) => Status::ServiceUnavailable }
}

struct JsonLog;
#[derive(Clone)]
struct Ctx(Option<Instant>, String);

fn valid_rid(s: &str) -> bool { !s.is_empty() && s.len() <= 128 && s.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b)) }

#[rocket::async_trait]
impl Fairing for JsonLog {
    fn info(&self) -> Info { Info { name: "JSON request log + request id", kind: Kind::Request | Kind::Response } }
    async fn on_request(&self, req: &mut Request<'_>, _: &mut Data<'_>) {
        let rid = req.headers().get_one("x-request-id").filter(|s| valid_rid(s)).map(str::to_owned)
            .unwrap_or_else(|| uuid::Uuid::new_v4().simple().to_string());
        req.local_cache(|| Ctx(Some(Instant::now()), rid));
    }
    async fn on_response<'r>(&self, req: &'r Request<'_>, res: &mut Response<'r>) {
        let c = req.local_cache(|| Ctx(None, String::new()));
        res.set_header(Header::new("x-request-id", c.1.clone()));
        let ms = c.0.map(|s| s.elapsed().as_secs_f64() * 1e3).unwrap_or(0.0);
        tracing::info!(method = %req.method(), route = req.route().map(|r| r.uri.as_str()).unwrap_or(""),
            path = %req.uri().path(), status = res.status().code, duration_ms = ms, request_id = %c.1, "request");
    }
}

#[launch]
fn rocket() -> _ {
    let (writer, guard) = tracing_appender::non_blocking(std::io::stdout());
    Box::leak(Box::new(guard));
    tracing_subscriber::fmt().json().with_env_filter("bench=info").with_writer(writer).with_target(false).init();
    let prometheus = PrometheusMetrics::new();
    rocket::build()
        .attach(Db::init())
        .attach(prometheus.clone())
        .attach(JsonLog)
        .mount("/", routes![plaintext, json, echo, db, healthz, readyz])
        .mount("/metrics", prometheus)
}
