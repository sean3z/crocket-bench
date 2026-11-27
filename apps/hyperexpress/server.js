// TFB json + db on hyper-express 7 (uWebSockets.js) + node-postgres, production track. Single Node process.
// Built in: body size limit (max_body_length -> 413), uWS idle/slow-header timeout (~10 s).
// Added for parity: pino JSON access log, prom-client histogram, x-request-id, /healthz + /readyz,
// hand-written /echo contract validation, 10 s handler timeout, SIGTERM drain (auto_close off + shutdown()).
import HyperExpress from 'hyper-express';
import pg from 'pg';
import pino from 'pino';
import client from 'prom-client';
import { randomUUID } from 'node:crypto';

const log = pino({ level: 'info' });
const pool = new pg.Pool({
  connectionString: process.env.DATABASE_URL ?? 'postgresql://bench:bench@localhost/bench',
  max: Number(process.env.POOL_SIZE ?? 16), min: Number(process.env.POOL_SIZE ?? 16),
  statement_timeout: 10000,
});

client.collectDefaultMetrics();
const hist = new client.Histogram({ name: 'http_request_duration_seconds', help: 'request duration',
  labelNames: ['method', 'route', 'status'] });

const RID = /^[A-Za-z0-9._-]{1,128}$/;
// auto_close defaults to true, which force-closes every socket (in-flight requests too) on SIGTERM.
// Turn it off and use the built-in shutdown(), which stops listening and waits for active requests.
const app = new HyperExpress.Server({ max_body_length: 1 << 20, fast_abort: false, auto_close: false });
let draining = false;

app.use((req, res, next) => {
  const t0 = process.hrtime.bigint();
  const h = req.headers['x-request-id'];
  const id = h && RID.test(h) ? h : randomUUID();
  res.header('x-request-id', id);
  if (draining) res.header('connection', 'close');
  let done = false;
  const fin = () => {
    if (done) return; done = true;
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    const route = req.route?.pattern ?? req.path;
    hist.labels(req.method, route, String(res.statusCode ?? 200)).observe(ms / 1000);
    log.info({ method: req.method, route, path: req.path, status: res.statusCode ?? 200, duration_ms: ms, request_id: id }, 'request');
  };
  res.once('finish', fin); res.once('close', fin);
  next();
});

const withTimeout = (p, ms = 10000) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms).unref())]);
const rid = () => 1 + Math.floor(Math.random() * 10000);
const bad = (res, msg) => res.status(400).json({ error: msg });

app.post('/echo', async (req, res) => {
  let b;
  try { b = JSON.parse(await req.text()); } catch { return bad(res, 'malformed json'); }
  if (b === undefined || b === null || typeof b !== 'object' || Array.isArray(b)) return bad(res, 'body must be an object');
  for (const k of Object.keys(b)) if (k !== 'name' && k !== 'qty') return bad(res, `unknown field ${k}`);
  if (typeof b.name !== 'string') return bad(res, 'name must be a string');
  if (!Number.isInteger(b.qty)) return bad(res, 'qty must be an integer');
  res.json({ name: b.name, qty: b.qty });
});
app.get('/plaintext', (req, res) => { res.type('text/plain').send('Hello, World!'); });
app.get('/json', (req, res) => { res.json({ message: 'Hello, World!' }); });
app.get('/db', async (req, res) => {
  try {
    const r = await withTimeout(pool.query({ name: 'world', text: 'select id, randomnumber from world where id = $1', values: [rid()] }));
    res.json({ id: r.rows[0].id, randomNumber: r.rows[0].randomnumber });
  } catch (e) { res.status(e.message === 'timeout' ? 504 : 500).json({ error: 'db error' }); }
});
app.get('/healthz', (req, res) => { res.json({ status: 'ok' }); });
app.get('/readyz', async (req, res) => {
  if (draining) return res.status(503).json({ status: 'draining' });
  try { await withTimeout(pool.query('select 1'), 2000); res.json({ status: 'ready' }); } catch { res.status(503).json({ status: 'not ready' }); }
});
app.get('/metrics', async (req, res) => { res.type(client.register.contentType).send(await client.register.metrics()); });

const shutdown = async (sig) => {
  if (draining) return; draining = true;
  log.info({ signal: sig }, 'draining');
  const t = setTimeout(() => process.exit(1), 10000); t.unref();
  await app.shutdown();               // stop accepting, wait for in-flight requests
  await pool.end().catch(() => {});
  process.exit(0);
};
process.on('SIGTERM', shutdown); process.on('SIGINT', shutdown);

app.listen(Number(process.env.PORT ?? 8000), '0.0.0.0')
  .then(() => log.info({ port: Number(process.env.PORT ?? 8000) }, 'listening'))
  .catch((e) => { log.error(e, 'listen failed'); process.exit(1); });
