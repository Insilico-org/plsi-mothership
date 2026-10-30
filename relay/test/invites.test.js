import { test } from 'node:test';
import assert from 'node:assert/strict';
import { call, fakeEnv, ADMIN, IP } from './fake.js';

const admin = (env, path, body, token = ADMIN) => call(env, 'POST', path, { body, token });
const GRANT = { role: 'researcher', scopes: ['findings:submit', 'releases:read'], days: 365 };

async function invited(env, grant = GRANT) {
  const got = await (await admin(env, '/admin/invite', grant)).json();
  assert.equal(got.ok, true);
  return { ...got, code: new URL(got.link).hash.slice(1) };
}

const claim = (env, invite, turnstileToken = 'pro') => call(env, 'POST', '/pro/claim', { body: { invite, turnstileToken } });
const status = async (env, id) => (await (await admin(env, '/admin/invite/status', { ids: [id] })).json()).invites[id];

test('an invite needs ADMIN_KEY and a valid grant', async () => {
  const env = fakeEnv();
  assert.equal((await admin(env, '/admin/invite', GRANT, 'wrong')).status, 401);
  for (const bad of [{ ...GRANT, role: 'admin' }, { ...GRANT, scopes: [] }, { ...GRANT, scopes: ['all'] },
    { ...GRANT, scopes: ['releases:read', 'releases:read'] }, { ...GRANT, days: 0 }, { ...GRANT, days: 367 }]) {
    assert.equal((await admin(env, '/admin/invite', bad)).status, 400, JSON.stringify(bad));
  }
  assert.equal(env.RELAY_KV.data.size, 0);
});

test('the invite link carries the code in the fragment; the relay keeps only its HMAC', async () => {
  const env = fakeEnv();
  const inv = await invited(env);
  const url = new URL(inv.link);
  assert.equal(url.origin + url.pathname, 'https://relay.test/pro');
  assert.equal(url.search, '');
  assert.ok(inv.code.length >= 32);
  assert.ok(Math.abs(inv.exp - (Date.now() / 1000 + 72 * 3600)) < 5);
  for (const [k, v] of env.RELAY_KV.data) assert.ok(!k.includes(inv.code) && !v.value.includes(inv.code), k);
  assert.deepEqual(await status(env, inv.id), { status: 'pending' });
});

test('opening the pro page spends nothing and shows Turnstile', async () => {
  const env = fakeEnv();
  const inv = await invited(env);
  const page = await (await call(env, 'GET', `/pro#${inv.code}`)).text();
  assert.match(page, /cf-turnstile/);
  assert.match(page, /data-action="pro"/);
  assert.match(page, /MOTHERSHIP_PROFESSIONAL_TOKEN/);
  assert.match(await (await call(fakeEnv({ TURNSTILE_SITE_KEY: '' }), 'GET', '/pro')).text(), /Misconfigured/);
  assert.deepEqual(await status(env, inv.id), { status: 'pending' });
});

test('a claim needs Turnstile, mints once, and the token works then revokes', async () => {
  const env = fakeEnv();
  const inv = await invited(env);
  assert.equal((await (await claim(env, inv.code, '')).json()).error, 'turnstile_required');
  assert.equal((await (await claim(env, inv.code, 'bad')).json()).error, 'turnstile_failed');
  assert.deepEqual(await status(env, inv.id), { status: 'pending' });

  const res = await claim(env, inv.code);
  assert.equal(res.status, 200);
  assert.equal(res.headers.get('cache-control'), 'no-store');
  const got = await res.json();
  assert.equal(got.role, 'researcher');
  assert.deepEqual(got.scopes, GRANT.scopes);
  const [tag, body] = got.token.split('.');
  assert.equal(tag, 'plsp1');
  assert.equal(Buffer.from(body, 'base64url').toString(),
    JSON.stringify({ pid: got.pid, role: 'researcher', scopes: GRANT.scopes, exp: got.exp }));
  assert.ok(Math.abs(got.exp - (Date.now() / 1000 + 365 * 86400)) < 5);

  const again = await claim(env, inv.code);
  assert.equal(again.status, 400);
  assert.equal((await again.json()).error, 'invite_already_used');
  const st = await status(env, inv.id);
  assert.equal(st.status, 'claimed');
  assert.equal(st.pid, got.pid);

  const finding = { kind: 'plsi_finding', schema_version: 4, professional_id: got.pid };
  assert.equal((await call(env, 'POST', '/relay', { body: finding, token: got.token })).status, 202);
  await admin(env, '/admin/revoke', { id: got.pid });
  assert.equal((await (await call(env, 'POST', '/relay', { body: finding, token: got.token })).json()).error, 'token_revoked');
});

test('a raced second claim gets the same pid, so one revoke stops both tokens', async () => {
  const env = fakeEnv();
  const inv = await invited(env);
  const [key, entry] = [...env.RELAY_KV.data].find(([k]) => k.startsWith('invite:'));
  const first = await (await claim(env, inv.code)).json();
  env.RELAY_KV.data.set(key, entry); // the second claim read the invite before the first one's write landed
  const second = await (await claim(env, inv.code)).json();
  assert.match(first.pid, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  assert.equal(second.pid, first.pid);
  await admin(env, '/admin/revoke', { id: first.pid });
  const finding = { kind: 'plsi_finding', schema_version: 4, professional_id: first.pid };
  for (const t of [first.token, second.token]) {
    assert.equal((await (await call(env, 'POST', '/relay', { body: finding, token: t })).json()).error, 'token_revoked');
  }
});

test('unknown, expired and malformed invites fail', async () => {
  const env = fakeEnv();
  assert.equal((await (await claim(env, 'x'.repeat(32))).json()).error, 'invalid_or_expired_invite');
  assert.equal((await (await claim(env, '')).json()).error, 'invalid_invite');
  const inv = await invited(env);
  const [key, entry] = [...env.RELAY_KV.data].find(([k]) => k.startsWith('invite:'));
  env.RELAY_KV.data.set(key, { ...entry, value: JSON.stringify({ ...JSON.parse(entry.value), exp: 1 }) });
  assert.equal((await (await claim(env, inv.code)).json()).error, 'invalid_or_expired_invite');
  assert.deepEqual(await status(env, inv.id), { status: 'expired' });
  assert.deepEqual(await status(env, 'a'.repeat(43)), { status: 'unknown' });
  assert.equal((await admin(env, '/admin/invite/status', { ids: ['../x'] })).status, 400);
  assert.equal((await admin(env, '/admin/invite/status', { ids: Array(101).fill('a'.repeat(43)) })).status, 400);
});

test('claims are rate-limited per hashed IP', async () => {
  const env = fakeEnv();
  for (let i = 0; i < 20; i++) await claim(env, 'y'.repeat(32));
  const res = await claim(env, 'y'.repeat(32));
  assert.equal(res.status, 429);
  assert.ok((await res.json()).retry_after >= 3599);
  for (const [k, v] of env.RELAY_KV.data) assert.ok(!k.includes(IP) && !v.value.includes(IP), k);
});

test('the direct mint is gone', async () => {
  assert.equal((await admin(fakeEnv(), '/admin/professional', GRANT)).status, 404);
});
