import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { calls, call, fakeEnv, installToken, professionalToken, ADMIN, INTAKE, INSTALL_ID, PID, SENDER, WEBHOOK } from './fake.js';

const msg = (kind, extra = {}) => ({ kind, schema_version: 4, ...extra });
const relay = (env, body, token) => call(env, 'POST', '/relay', { body, token });
const lastForward = () => calls.findLast((c) => c.url === WEBHOOK);

test('bad, revoked and expired tokens are 401', async () => {
  const env = fakeEnv();
  let res = await relay(env, msg('plsi_contribution'), 'plsi1.abc.def');
  assert.equal(res.status, 401);
  assert.equal((await res.json()).error, 'unauthorized');
  assert.equal((await relay(env, msg('plsi_contribution'))).status, 401);

  await env.RELAY_KV.put(`revoked:${INSTALL_ID}`, '1');
  res = await relay(env, msg('plsi_contribution'), installToken());
  assert.equal(res.status, 401);
  assert.equal((await res.json()).error, 'token_revoked');

  const old = professionalToken({ scopes: ['findings:submit'], exp: Math.floor(Date.now() / 1000) - 1 });
  res = await relay(env, msg('plsi_finding', { professional_id: PID }), old);
  assert.equal(res.status, 401);
  assert.equal((await res.json()).error, 'token_expired');
});

test('install tokens send contributor kinds only', async () => {
  const env = fakeEnv();
  for (const kind of ['plsi_contribution', 'plsi_withdrawal', 'plsi_registry_update']) {
    assert.equal((await relay(env, msg(kind), installToken())).status, 202, kind);
  }
  for (const kind of ['plsi_research_note', 'plsi_preregistration', 'plsi_finding', 'plsi_unknown', 'constructor', undefined]) {
    const res = await relay(env, msg(kind, { professional_id: PID }), installToken());
    assert.equal(res.status, 403, String(kind));
    assert.equal((await res.json()).error, 'kind_not_allowed');
  }
});

test('professional kinds follow scope', async () => {
  const env = fakeEnv();
  const read = professionalToken({ scopes: ['releases:read', 'findings:read'] });
  const submit = professionalToken({ scopes: ['findings:submit'] });
  const pre = msg('plsi_preregistration', { professional_id: PID });
  assert.equal((await relay(env, pre, read)).status, 403);
  const note = await relay(env, msg('plsi_research_note'), submit);
  assert.equal(note.status, 403);
  assert.equal((await note.json()).error, 'kind_not_allowed');
  assert.equal((await relay(env, pre, submit)).status, 202);
  assert.equal((await relay(env, msg('plsi_contribution'), submit)).status, 403);
});

test('professional_id must equal the token pid', async () => {
  const token = professionalToken({ scopes: ['findings:submit'] });
  const res = await relay(fakeEnv(), msg('plsi_finding', { professional_id: '00000000-2222-4333-8444-555555555555' }), token);
  assert.equal(res.status, 403);
  assert.equal((await res.json()).error, 'professional_id_mismatch');
});

test('forwards only a relay_ref with the relay key; the sender rides in the parked record, never the install id', async () => {
  const env = fakeEnv();
  const raw = '{"kind":"plsi_contribution",  "schema_version":4, "contributor_id":"ab"}';
  const res = await relay(env, raw, installToken());
  assert.equal(res.status, 202);
  const sent = lastForward();
  assert.deepEqual(await res.json(), { ok: true, ticket: JSON.parse(sent.init.body).ref });
  const ref = JSON.parse(sent.init.body);
  assert.equal(ref.kind, 'relay_ref');
  assert.deepEqual(JSON.parse(env.RELAY_KV.data.get(`body:${ref.ref}`).value), { body: raw, sender: 'contributor' });
  assert.deepEqual(sent.init.headers, { 'content-type': 'application/json', authorization: `Bearer ${SENDER}` });
  assert.ok(![...env.RELAY_KV.data.values()].some((v) => v.value.includes(INSTALL_ID)));

  await relay(env, msg('plsi_finding', { professional_id: PID }), professionalToken({ role: 'physician', scopes: ['findings:submit'] }));
  const pro = JSON.parse(lastForward().init.body);
  const parked = JSON.parse(env.RELAY_KV.data.get(`body:${pro.ref}`).value);
  assert.deepEqual([parked.sender, parked.professional_id, parked.role], ['professional', PID, 'physician']);
  assert.ok(!JSON.stringify(lastForward().init.headers).includes(PID));
});

test('every body is parked and the Mother gets a relay_ref it can take once', async () => {
  const env = fakeEnv();
  const raw = JSON.stringify(msg('plsi_contribution', { pad: 'é'.repeat(3000) }));
  const bytes = Buffer.byteLength(raw);
  assert.equal((await relay(env, raw, installToken())).status, 202);
  const sent = lastForward();
  assert.ok(sent.init.body.length < 300);
  const ref = JSON.parse(sent.init.body);
  assert.deepEqual(Object.keys(ref), ['kind', 'schema_version', 'ref', 'sha256', 'bytes']);
  assert.equal(ref.kind, 'relay_ref');
  assert.equal(ref.schema_version, 4);
  assert.equal(ref.bytes, bytes);
  assert.equal(ref.sha256, createHash('sha256').update(raw).digest('hex'));
  assert.equal(env.RELAY_KV.data.get(`body:${ref.ref}`).ttl, 86400);

  const take = (r, token = INTAKE) => call(env, 'POST', '/admin/body', { body: { ref: r }, token });
  assert.equal((await take(ref.ref, installToken())).status, 401);
  assert.equal((await take(ref.ref, ADMIN)).status, 401);
  const got = await take(ref.ref);
  assert.equal(got.status, 200);
  assert.deepEqual(await got.json(), { ok: true, body: raw, sender: 'contributor' });
  assert.equal(env.RELAY_KV.data.has(`body:${ref.ref}`), false);
  const again = await take(ref.ref);
  assert.equal(again.status, 404);
  assert.equal((await again.json()).error, 'ref_not_found');
  assert.equal((await take('../x')).status, 400);
});

test('the sender gets a ticket and reads the Mother ACK with it', async () => {
  const env = fakeEnv();
  const res = await relay(env, msg('plsi_contribution'), installToken());
  assert.equal(res.status, 202);
  const { ticket } = await res.json();
  assert.equal(ticket, JSON.parse(lastForward().init.body).ref);

  const read = (t, token = installToken()) => call(env, 'POST', '/ack', { body: { ticket: t }, token });
  assert.deepEqual(await (await read(ticket)).json(), { ok: true, status: 'pending' });
  assert.equal((await read(ticket, null)).status, 401);
  assert.equal((await read('../x')).status, 400);

  const post = (body, token = INTAKE) => call(env, 'POST', '/admin/ack', { body, token });
  const theAck = { ok: true, receipt: 'r_00000000000000aa', kind: 'plsi_contribution', status: 'active' };
  assert.equal((await post({ ref: ticket, ack: theAck }, installToken())).status, 401);
  assert.equal((await post({ ref: ticket, ack: theAck }, ADMIN)).status, 401);
  assert.equal((await post({ ref: ticket, ack: 'x' })).status, 400);
  assert.equal((await post({ ref: ticket, ack: { pad: 'x'.repeat(5000) } })).status, 413);
  assert.equal((await post({ ref: ticket, ack: theAck })).status, 200);
  assert.equal(env.RELAY_KV.data.get(`ack:${ticket}`).ttl, 7 * 86400);
  assert.deepEqual(await (await read(ticket)).json(), { ok: true, status: 'done', ack: theAck });
  assert.ok(![...env.RELAY_KV.data.keys()].some((k) => k.includes(INSTALL_ID)));
});

test('a parked body is dropped when the Mother cannot be reached', async () => {
  const env = fakeEnv({ MOTHERSHIP_WEBHOOK_URL: 'https://down.test/intake' });
  const real = globalThis.fetch;
  globalThis.fetch = async (url, init) => (String(url).includes('down.test') ? Promise.reject(new TypeError('dns')) : real(url, init));
  try {
    const res = await relay(env, JSON.stringify(msg('plsi_contribution', { pad: 'x'.repeat(5000) })), installToken());
    assert.equal(res.status, 503);
    assert.ok(![...env.RELAY_KV.data.keys()].some((k) => k.startsWith('body:')));
  } finally {
    globalThis.fetch = real;
  }
});

test('body limit and bad JSON', async () => {
  const env = fakeEnv();
  const big = JSON.stringify(msg('plsi_contribution', { pad: 'x'.repeat(256 * 1024) }));
  assert.equal((await relay(env, big, installToken())).status, 413);
  const res = await relay(env, 'not json', installToken());
  assert.equal(res.status, 400);
  assert.equal((await res.json()).error, 'bad_json');
});

test('only MOTHERSHIP_SENDER_KEY is used', async () => {
  const env = fakeEnv({ MOTHERSHIP_SENDER_KEY: undefined, MOTHERSHIP_SENDER_KEY_V2: 'x', MOTHERSHIP_SENDER_KEY_OK: 'y' });
  const res = await relay(env, msg('plsi_contribution'), installToken());
  assert.equal(res.status, 503);
  assert.equal((await res.json()).error, 'relay_not_configured');
});

test('relay rate-limit key holds no install id', async () => {
  const env = fakeEnv();
  await relay(env, msg('plsi_contribution'), installToken());
  const keys = [...env.RELAY_KV.data.keys()];
  assert.ok(keys.some((k) => k.startsWith('rl:relay:')));
  assert.ok(!keys.some((k) => k.includes(INSTALL_ID)));
});

test('missing TOKEN_HMAC_SECRET is 503, not a token rejection', async () => {
  const env = fakeEnv({ TOKEN_HMAC_SECRET: undefined });
  for (const res of [await relay(env, msg('plsi_contribution'), installToken()),
    await call(env, 'GET', '/public/releases/latest', { token: professionalToken({ scopes: ['releases:read'] }) })]) {
    assert.equal(res.status, 503);
    assert.equal((await res.json()).error, 'relay_not_configured');
  }
});

test('an unreachable Mother is a JSON 503', async () => {
  const env = fakeEnv({ MOTHERSHIP_WEBHOOK_URL: 'https://down.test/intake' });
  const real = globalThis.fetch;
  globalThis.fetch = async (url, init) => (String(url).includes('down.test') ? Promise.reject(new TypeError('dns')) : real(url, init));
  try {
    const res = await relay(env, msg('plsi_contribution'), installToken());
    assert.equal(res.status, 503);
    assert.equal((await res.json()).error, 'upstream_unavailable');
  } finally {
    globalThis.fetch = real;
  }
});

test('a KV write limit is 429, not a 500', async () => {
  const env = fakeEnv();
  env.RELAY_KV.put = async () => { throw new Error('KV PUT failed: 429 Too Many Requests'); };
  const res = await relay(env, msg('plsi_contribution'), installToken());
  assert.equal(res.status, 429);
  assert.deepEqual(await res.json(), { ok: false, error: 'rate_limited', retry_after: 60 }); // window end unknown
  assert.equal(res.headers.get('retry-after'), '60');
});
