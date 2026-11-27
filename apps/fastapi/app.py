# TFB plaintext/json/db on FastAPI + uvicorn (4 workers = 1 per core, uvloop + httptools) + asyncpg, production track.
# Added for parity: prometheus-fastapi-instrumentator (multiprocess /metrics histogram), asgi-correlation-id
# (x-request-id), python-json-logger (one JSON line per request from an ASGI middleware), a 10 s request
# timeout middleware + asyncpg command_timeout, /healthz + /readyz routes. Pool 4 per worker = 16 total.
import asyncio, logging, os, random, sys, time
import asyncpg
from asgi_correlation_id import CorrelationIdMiddleware, correlation_id
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import ORJSONResponse, PlainTextResponse, Response
from prometheus_fastapi_instrumentator import Instrumentator
from pythonjsonlogger.json import JsonFormatter

h = logging.StreamHandler(sys.stdout); h.setFormatter(JsonFormatter("%(asctime)s %(levelname)s %(message)s"))
log = logging.getLogger("request"); log.addHandler(h); log.setLevel(logging.INFO); log.propagate = False

pool = None
app = FastAPI(default_response_class=ORJSONResponse, docs_url=None, redoc_url=None, openapi_url=None)

@app.on_event("startup")
async def startup():
    global pool
    n = int(os.environ.get("POOL_PER_WORKER", "4"))
    pool = await asyncpg.create_pool(os.environ.get("DATABASE_URL", "postgresql://bench:bench@127.0.0.1/bench"),
                                     min_size=n, max_size=n, command_timeout=10)

@app.get("/plaintext", response_class=PlainTextResponse)
async def plaintext(): return "Hello, World!"

@app.get("/json")
async def json_(): return {"message": "Hello, World!"}

class Item(BaseModel):
    name: str
    qty: int

@app.post("/echo")
async def echo(item: Item): return item

@app.get("/db")
async def db():
    row = await pool.fetchrow("select id, randomnumber from world where id = $1", random.randint(1, 10000))
    return {"id": row[0], "randomNumber": row[1]}

@app.get("/healthz", response_class=PlainTextResponse)
async def healthz(): return "ok"

@app.get("/readyz", response_class=PlainTextResponse)
async def readyz():
    try:
        await pool.fetchval("select 1"); return "ready"
    except Exception:
        return PlainTextResponse("not ready", status_code=503)

class LogAndTimeout:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http": return await self.app(scope, receive, send)
        t0 = time.perf_counter(); status = [500]
        async def snd(m):
            if m["type"] == "http.response.start": status[0] = m["status"]
            await send(m)
        try:
            async with asyncio.timeout(10): await self.app(scope, receive, snd)
        except TimeoutError:
            await Response(status_code=504)(scope, receive, send); status[0] = 504
        route = scope.get("route"); 
        log.info("request", extra={"method": scope["method"], "route": getattr(route, "path", ""), "path": scope["path"],
                 "status": status[0], "duration_ms": round((time.perf_counter() - t0) * 1e3, 3), "request_id": correlation_id.get()})

app.add_middleware(LogAndTimeout)
Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
app.add_middleware(CorrelationIdMiddleware, header_name="x-request-id", validator=None,
                   generator=lambda: os.urandom(16).hex())
