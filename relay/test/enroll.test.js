import { test } from 'node:test';
import assert from 'node:assert/strict';
import { calls, call, fakeEnv, enrollCode, sign, b64url, IP } from './fake.js';

test('join page always shows Turnstile, or says misconfigured', async () => {
  const page = await (await call(fakeEnv(), 'GET', '/join')).text();
  assert.match(page, /cf-turnstile/);
  assert.match(page, /plsi mothership join/);
  const bare = await (await call(fakeEnv({ TURNSTILE_SITE_KEY: '' }), 'GET', '/join')).text();
  assert.match(bare, /Misconfigured/);
});

test('join/issue requires Turnstile with no env switch', async () => {
  const env = fakeEnv({ TURNSTILE_REQUIRED: '0' });
  let res = await call(env, 'POST', '/join/issue', { body: {} });
  assert.equal(res.status, 403);
  assert.equal((await res.json()).error, 'turnstile_required');
  res = await call(env, 'POST', '/join/issue', { body: { turnstileToken: 'bad' } });
  assert.equal((await res.json()).error, 'turnstile_failed');
  res = await call(fakeEnv({ TURNSTILE_SECRET_KEY: undefined }), 'POST', '/join/issue', { body: { turnstileToken: 'join' } });
  assert.equal(res.status, 503);
});

test('issue then enroll; a reused code fails', async () => {
  const env = fakeEnv();
  const issued = await (await call(env, 'POST', '/join/issue', { body: { turnstileToken: 'join' } })).json();
  assert.equal(issued.ok, true);
  assert.match(issued.code, /^e1\.[^.]+\.\d+\.[^.]+$/);
  assert.equal(issued.expires_in_sec, 900);
  assert.equal(calls.findLast((c) => c.url.includes('siteverify')).init.body.get('secret'), 'turnstile-secret');

  const res = await call(env, 'POST', '/enroll', { body: { code: issued.code } });
  const got = await res.json();
  assert.equal(res.status, 200);
  assert.match(got.install_id, /^[0-9a-f-]{36}$/);
  assert.equal(got.token, `plsi1.${b64url(got.install_id)}.${sign(`v1:${got.install_id}`)}`);

  const again = await call(env, 'POST', '/enroll', { body: { code: issued.code } });
  assert.equal(again.status, 400);
  assert.equal((await again.json()).error, 'code_already_used');
});

test('expired or forged codes fail', async () => {
  const env = fakeEnv();
  let res = await call(env, 'POST', '/enroll', { body: { code: enrollCode(Math.floor(Date.now() / 1000) - 5) } });
  assert.equal((await res.json()).error, 'invalid_or_expired_code');
  res = await call(env, 'POST', '/enroll', { body: { code: `${enrollCode()}x` } });
  assert.equal((await res.json()).error, 'invalid_or_expired_code');
});

test('rate-limit keys hold no raw IP', async () => {
  const env = fakeEnv();
  await call(env, 'POST', '/join/issue', { body: { turnstileToken: 'join' } });
  await call(env, 'POST', '/enroll', { body: { code: enrollCode() } });
  const keys = [...env.RELAY_KV.data.keys()];
  assert.ok(keys.some((k) => k.startsWith('rl:enroll:')));
  for (const [k, v] of env.RELAY_KV.data) assert.ok(!k.includes(IP) && !v.value.includes(IP), k);
  const day = new Date().toISOString().slice(0, 10);
  assert.ok(keys.includes(`rl:join:${sign(`ip:${day}:${IP}`)}`));
});

test('missing KV is 503 relay_not_configured wherever it is needed', async () => {
  const env = fakeEnv({ RELAY_KV: undefined });
  for (const [method, path] of [['POST', '/join/issue'], ['POST', '/enroll'], ['POST', '/relay'],
    ['GET', '/public/catalog'], ['POST', '/admin/revoke']]) {
    const res = await call(env, method, path, { body: method === 'POST' ? {} : undefined });
    assert.equal(res.status, 503, path);
    assert.equal((await res.json()).error, 'relay_not_configured');
  }
  assert.equal((await call(env, 'GET', '/join')).status, 200);
});

test('wrong method is 405', async () => {
  const res = await call(fakeEnv(), 'GET', '/enroll');
  assert.equal(res.status, 405);
  assert.equal((await res.json()).ok, false);
});
