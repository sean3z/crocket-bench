// TFB json + db on Fastify 5 + node-postgres, production track. Single Node process (one per pod, scaled out).
// Added for parity: prom-client (metrics), /healthz + /readyz routes, x-request-id echo; pino logs are built in.
import Fastify from 'fastify';
import pg from 'pg';
import client from 'prom-client';
import { randomUUID } from 'node:crypto';

const pool = new pg.Pool({
  connectionString: process.env.DATABASE_URL ?? 'postgresql://bench:bench@localhost/bench',
  max: Number(process.env.POOL_SIZE ?? 16), min: Number(process.env.POOL_SIZE ?? 16),
  statement_timeout: 10000,
});

client.collectDefaultMetrics();
const hist = new client.Histogram({ name: 'http_request_duration_seconds', help: 'request duration',
  labelNames: ['method', 'route', 'status'] });

const RID = /^[A-Za-z0-9._-]{1,128}$/;
const app = Fastify({ logger: { level: 'info' }, disableRequestLogging: true,
  requestIdHeader: 'x-request-id', genReqId: () => randomUUID(),
  connectionTimeout: 10000, requestTimeout: 10000, keepAliveTimeout: 75000, bodyLimit: 1 << 20 });

app.addHook('onRequest', async (req, reply) => {
  if (!RID.test(String(req.id))) req.id = randomUUID();
  reply.header('x-request-id', req.id);
});
app.addHook('onResponse', async (req, reply) => {
  const route = req.routeOptions.url ?? '';
  hist.labels(req.method, route, String(reply.statusCode)).observe(reply.elapsedTime / 1000);
  req.log.info({ method: req.method, route, path: req.url, status: reply.statusCode,
    duration_ms: reply.elapsedTime, request_id: req.id }, 'request');
});

const msg = { type: 'object', properties: { message: { type: 'string' } } };
const world = { type: 'object', properties: { id: { type: 'integer' }, randomNumber: { type: 'integer' } } };
const rid = () => 1 + Math.floor(Math.random() * 10000);

const item = { type: 'object', required: ['name', 'qty'], properties: { name: { type: 'string' }, qty: { type: 'integer' } }, additionalProperties: false };
app.post('/echo', { schema: { body: item, response: { 200: item } } }, async (req) => req.body);
app.get('/plaintext', async (req, reply) => reply.type('text/plain').send('Hello, World!'));
app.get('/json', { schema: { response: { 200: msg } } }, async () => ({ message: 'Hello, World!' }));
app.get('/db', { schema: { response: { 200: world } } }, async () => {
  const r = await pool.query({ name: 'world', text: 'select id, randomnumber from world where id = $1', values: [rid()] });
  return { id: r.rows[0].id, randomNumber: r.rows[0].randomnumber };
});
app.get('/healthz', async () => ({ status: 'ok' }));
app.get('/readyz', async (req, reply) => {
  try { await pool.query('select 1'); return { status: 'ready' }; } catch { return reply.code(503).send({ status: 'not ready' }); }
});
app.get('/metrics', async (req, reply) => reply.type(client.register.contentType).send(await client.register.metrics()));

app.listen({ host: '0.0.0.0', port: Number(process.env.PORT ?? 8000) });
