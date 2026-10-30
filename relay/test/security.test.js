import { test } from 'node:test';
import assert from 'node:assert/strict';
import { calls, call, endless, fakeEnv, installToken, professionalToken, mother, sign, ADMIN, INTAKE, IP, PID, WEBHOOK } from './fake.js';
import { ipBucket } from '../public/_worker.js/limits.js';

const contribution = { kind: 'plsi_contribution', schema_version: 4 };
const relay = (env, token, body = contribution) => call(env, 'POST', '/relay', { body, token });
const forwards = () => calls.filter((c) => c.url === WEBHOOK).length;
const REF = '11111111-2222-4333-8444-000000000000';

test('enrollment is closed by default: 503 enrollment_closed and a plain page; tokens keep working', async () => {
  const env = fakeEnv({ ENROLLMENT_OPEN: undefined });
  const checks = calls.filter((c) => c.url.includes('siteverify')).length;
  for (const path of ['/join/issue', '/enroll', '/pro/claim']) {
    const res = await call(env, 'POST', path, { body: { turnstileToken: 'join', code: 'x', invite: 'y' } });
    assert.equal(res.status, 503, path);
    assert.equal((await res.json()).error, 'enrollment_closed');
  }
  for (const path of ['/join', '/pro']) {
    const res = await call(env, 'GET', path);
    assert.equal(res.status, 503, path);
    const page = await res.text();
    assert.match(page, /Not open yet/);
    assert.doesNotMatch(page, /cf-turnstile/);
    assert.match(res.headers.get('content-security-policy'), /frame-ancestors 'none'/);
  }
  assert.equal(calls.filter((c) => c.url.includes('siteverify')).length, checks);
  assert.equal(env.RELAY_KV.data.size, 0);
  assert.equal((await call(fakeEnv({ ENROLLMENT_OPEN: 'true' }), 'POST', '/enroll', { body: {} })).status, 503);
  assert.equal((await relay(env, installToken())).status, 202);
  assert.equal((await call(env, 'POST', '/ack', { body: { ticket: REF }, token: installToken() })).status, 200);
  await call(env, 'POST', '/admin/publish', { body: { what: 'catalog', body: {} }, token: ADMIN });
  assert.equal((await call(env, 'GET', '/public/catalog')).status, 200);
});

test('ENROLLMENT_OPEN=1 opens join and enroll', async () => {
  const env = fakeEnv({ ENROLLMENT_OPEN: '1' });
  assert.equal((await call(env, 'GET', '/join')).status, 200);
  const issued = await (await call(env, 'POST', '/join/issue', { body: { turnstileToken: 'join' } })).json();
  assert.equal((await call(env, 'POST', '/enroll', { body: { code: issued.code } })).status, 200);
});

test('INTAKE_KEY opens only body and ack; ADMIN_KEY opens everything else and not body/ack', async () => {
  const env = fakeEnv();
  const post = (path, body, token) => call(env, 'POST', path, { body, token });
  for (const path of ['/admin/body', '/admin/ack']) {
    const body = { ref: REF, ack: { ok: true } };
    assert.equal((await post(path, body, ADMIN)).status, 401, path);
    assert.notEqual((await post(path, body, INTAKE)).status, 401, path);
  }
  for (const [path, body] of [['/admin/invite', { role: 'researcher', scopes: ['releases:read'], days: 1 }],
    ['/admin/invite/status', { ids: ['a'.repeat(43)] }], ['/admin/revoke', { id: PID }],
    ['/admin/publish', { what: 'catalog', body: {} }]]) {
    assert.equal((await post(path, body, INTAKE)).status, 401, path);
    assert.equal((await post(path, body, ADMIN)).status, 200, path);
  }
  const noIntake = fakeEnv({ INTAKE_KEY: undefined });
  const res = await call(noIntake, 'POST', '/admin/body', { body: { ref: REF }, token: ADMIN });
  assert.equal(res.status, 503);
  assert.equal((await res.json()).error, 'intake_not_configured');
  assert.equal((await call(noIntake, 'POST', '/admin/revoke', { body: { id: PID }, token: ADMIN })).status, 200);
});

test('INTAKE_KEY equal to ADMIN_KEY closes every admin and intake route', async () => {
  const env = fakeEnv({ INTAKE_KEY: ADMIN });
  for (const path of ['/admin/body', '/admin/ack', '/admin/invite', '/admin/invite/status', '/admin/revoke',
    '/admin/publish']) {
    const res = await call(env, 'POST', path, { body: { ref: REF, id: PID }, token: ADMIN });
    assert.equal(res.status, 503, path);
    assert.equal((await res.json()).error, 'admin_not_configured', path);
  }
});

test('an install token relays 10 per hour, a professional 30; rejected kinds do not count', async () => {
  const env = fakeEnv();
  for (let i = 0; i < 5; i++) assert.equal((await relay(env, installToken(), { kind: 'plsi_finding' })).status, 403);
  for (let i = 0; i < 10; i++) assert.equal((await relay(env, installToken())).status, 202, String(i));
  const res = await relay(env, installToken());
  assert.equal(res.status, 429);
  const body = await res.json();
  assert.equal(body.error, 'rate_limited');
  assert.ok(body.retry_after >= 3599 && body.retry_after <= 3600, String(body.retry_after));
  assert.equal(res.headers.get('retry-after'), String(body.retry_after));

  const pro = professionalToken({ scopes: ['findings:submit'] });
  const finding = { kind: 'plsi_finding', schema_version: 4, professional_id: PID };
  for (let i = 0; i < 30; i++) assert.equal((await relay(env, pro, finding)).status, 202, String(i));
  assert.equal((await relay(env, pro, finding)).status, 429);
});

test('limits are fixed windows: hits never move the expiry, and a new window starts after an hour', async () => {
  const env = fakeEnv();
  await relay(env, installToken());
  const key = [...env.RELAY_KV.data.keys()].find((k) => k.startsWith('rl:relay:'));
  const first = env.RELAY_KV.data.get(key);
  const { start } = JSON.parse(first.value);
  assert.equal(first.expiration, start + 3600);
  assert.equal(first.ttl, undefined);
  await relay(env, installToken());
  const second = env.RELAY_KV.data.get(key);
  assert.deepEqual(JSON.parse(second.value), { count: 2, start });
  assert.equal(second.expiration, first.expiration);

  // A full window whose hour is over (the record outlived its expiry, e.g. KV lag) counts from zero again.
  env.RELAY_KV.data.set(key, { value: JSON.stringify({ count: 10, start: start - 3600 }) });
  assert.equal((await relay(env, installToken())).status, 202);
  assert.equal(JSON.parse(env.RELAY_KV.data.get(key).value).count, 1);
  env.RELAY_KV.data.set(key, { value: JSON.stringify({ count: 10, start }) });
  assert.equal((await relay(env, installToken())).status, 429);
  // retry_after counts down to the window's end, never below 1
  const t = Math.floor(Date.now() / 1000);
  env.RELAY_KV.data.set(key, { value: JSON.stringify({ count: 10, start: t - 3000 }) });
  const late = await relay(env, installToken());
  assert.ok(Math.abs((await late.json()).retry_after - 600) <= 1);
  env.RELAY_KV.data.set(key, { value: JSON.stringify({ count: 10, start: t - 3599 }) });
  assert.equal((await relay(env, installToken())).headers.get('retry-after'), '1');
});

test('/ack reads are 60 per hour per token', async () => {
  const env = fakeEnv();
  const read = (token) => call(env, 'POST', '/ack', { body: { ticket: REF }, token });
  for (let i = 0; i < 60; i++) assert.equal((await read(installToken())).status, 200, String(i));
  const over = await read(installToken());
  assert.equal(over.status, 429);
  assert.ok(Number(over.headers.get('retry-after')) >= 3599);
  assert.equal((await read(installToken('22222222-2222-4333-8444-555555555555'))).status, 200);
});

test('at most 200 webhook forwards per hour across all tokens', async () => {
  const env = fakeEnv();
  await env.RELAY_KV.put('rl:forward', JSON.stringify({ count: 199, start: Math.floor(Date.now() / 1000) }));
  const before = forwards();
  assert.equal((await relay(env, installToken())).status, 202);
  const res = await relay(env, installToken('22222222-2222-4333-8444-555555555555'));
  assert.equal(res.status, 429);
  assert.ok((await res.json()).retry_after >= 3599); // the global window's end
  assert.equal(forwards(), before + 1);
  const tokenCounts = [...env.RELAY_KV.data].filter(([k]) => k.startsWith('rl:relay:'));
  assert.equal(tokenCounts.length, 1); // the refused one cost its token nothing
});

test('IPv6 clients are limited per /64', async () => {
  assert.equal(ipBucket('2001:db8:1:2:aaaa::1'), '2001:db8:1:2::/64');
  assert.equal(ipBucket('2001:0db8:0001:0002:ffff:ffff:ffff:ffff'), '2001:db8:1:2::/64');
  assert.equal(ipBucket('2001:db8::1'), '2001:db8:0:0::/64');
  assert.equal(ipBucket('::ffff:203.0.113.7'), '203.0.113.7');
  assert.equal(ipBucket(IP), IP);

  const env = fakeEnv();
  for (let i = 0; i < 20; i++) {
    await call(env, 'POST', '/join/issue', { body: { turnstileToken: 'join' }, ip: `2001:db8:1:2::${i.toString(16)}` });
  }
  const res = await call(env, 'POST', '/join/issue', { body: { turnstileToken: 'join' }, ip: '2001:db8:1:2:ffff:1:2:3' });
  assert.equal(res.status, 429);
  assert.ok(Number(res.headers.get('retry-after')) >= 3599);
  const other = await call(env, 'POST', '/join/issue', { body: { turnstileToken: 'join' }, ip: '2001:db8:1:3::1' });
  assert.equal(other.status, 200);
  const day = new Date().toISOString().slice(0, 10);
  assert.ok(env.RELAY_KV.data.has(`rl:join:${sign(`ip:${day}:2001:db8:1:2::/64`)}`));
});

test('a failed Turnstile check costs no rate-limit write', async () => {
  const env = fakeEnv();
  for (let i = 0; i < 30; i++) await call(env, 'POST', '/join/issue', { body: { turnstileToken: 'bad' } });
  assert.ok(![...env.RELAY_KV.data.keys()].some((k) => k.startsWith('rl:')));
  assert.equal((await call(env, 'POST', '/join/issue', { body: { turnstileToken: 'join' } })).status, 200);
});

test('Turnstile needs the page action and this hostname', async () => {
  const env = fakeEnv();
  const issue = (t) => call(env, 'POST', '/join/issue', { body: { turnstileToken: t } });
  for (const t of ['pro', 'join@evil.test', 'pro@relay.test', '@relay.test']) {
    const res = await issue(t);
    assert.equal(res.status, 403, t);
    assert.equal((await res.json()).error, 'turnstile_failed');
  }
  assert.equal((await issue('join@relay.test')).status, 200);
  const claim = await call(env, 'POST', '/pro/claim', { body: { invite: 'z'.repeat(32), turnstileToken: 'join' } });
  assert.equal((await claim.json()).error, 'turnstile_failed');
});

test('oversize bodies are 413 before parsing, by content-length or by streaming past the cap', async () => {
  const env = fakeEnv();
  const big = JSON.stringify({ turnstileToken: 'join', code: 'x', invite: 'y', ticket: REF, pad: 'x'.repeat(4096) });
  for (const [path, token] of [['/join/issue'], ['/enroll'], ['/pro/claim'], ['/ack', installToken()]]) {
    assert.equal((await call(env, 'POST', path, { body: big, token })).status, 413, path);
    const declared = await call(env, 'POST', path, { body: '{}', token, headers: { 'content-length': '999999' } });
    assert.equal(declared.status, 413, `${path} content-length`);
    const { stream, pulled } = endless();
    const res = await call(env, 'POST', path, { body: stream, token });
    assert.equal(res.status, 413, `${path} stream`);
    assert.ok(pulled.n <= 8, `${path} read ${pulled.n} KiB`);
    assert.equal((await res.json()).error, 'body_too_large');
  }
  const { stream, pulled } = endless();
  assert.equal((await call(env, 'POST', '/relay', { body: stream, token: installToken() })).status, 413);
  assert.ok(pulled.n <= 260);
  const intake = await call(env, 'POST', '/admin/ack', { body: endless().stream, token: INTAKE });
  assert.equal(intake.status, 413);
});

test('an upstream error is 502 upstream_error with no upstream text and drops the parked body', async () => {
  const env = fakeEnv();
  try {
    for (const [status, text] of [[500, 'SECRET grok stack trace'], [401, '{"error":"bad sender key"}'], [302, '']]) {
      mother.reply = () => new Response(text, { status, headers: status === 302 ? { location: 'https://x.test' } : {} });
      const res = await relay(env, installToken(`3333333${status % 10}-2222-4333-8444-555555555555`));
      assert.equal(res.status, 502, String(status));
      const out = await res.text();
      assert.deepEqual(JSON.parse(out), { ok: false, error: 'upstream_error' });
      assert.ok(!out.includes('grok') && !out.includes('sender'));
    }
    assert.ok(![...env.RELAY_KV.data.keys()].some((k) => k.startsWith('body:')));

    for (const status of [200, 202, 204]) {
      mother.reply = () => new Response(status === 204 ? null : '{"run_id":"r1","note":"x"}', { status });
      const res = await relay(env, installToken(`4444444${status % 10}-2222-4333-8444-555555555555`));
      assert.equal(res.status, 202, String(status));
      const got = await res.json();
      assert.deepEqual(Object.keys(got), ['ok', 'ticket']);
      assert.equal(got.ok, true);
      assert.ok(env.RELAY_KV.data.has(`body:${got.ticket}`));
    }
  } finally {
    mother.reply = null;
  }
});

test('pages and static assets carry the security headers', async () => {
  const env = fakeEnv();
  for (const path of ['/join', '/pro', '/', '/relay.css']) {
    const res = await call(env, 'GET', path);
    assert.equal(res.status, 200, path);
    assert.equal(res.headers.get('content-security-policy'), "default-src 'self'; script-src 'self' 'unsafe-inline' "
      + 'https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; connect-src \'self\'; '
      + "frame-ancestors 'none'; base-uri 'none'", path);
    assert.equal(res.headers.get('x-content-type-options'), 'nosniff', path);
    assert.equal(res.headers.get('referrer-policy'), 'no-referrer', path);
  }
  const join = await (await call(env, 'GET', '/join')).text();
  assert.doesNotMatch(join, /<style|style="/); // the CSP has no inline styles: they live in /relay.css
  assert.doesNotMatch(await (await call(env, 'GET', '/')).text(), /wire v\d/);
});
