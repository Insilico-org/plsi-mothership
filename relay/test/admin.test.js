import { test } from 'node:test';
import assert from 'node:assert/strict';
import { call, fakeEnv, professionalToken, installToken, INSTALL_ID, ADMIN } from './fake.js';

const admin = (env, path, body, token = ADMIN) => call(env, 'POST', path, { body, token });
const get = (env, path, token) => call(env, 'GET', path, { token });

test('admin endpoints need ADMIN_KEY', async () => {
  const env = fakeEnv();
  assert.equal((await admin(env, '/admin/revoke', { id: INSTALL_ID }, 'wrong')).status, 401);
  assert.equal((await admin(env, '/admin/revoke', { id: INSTALL_ID }, installToken())).status, 401);
  assert.equal((await admin(env, '/admin/revoke', { id: INSTALL_ID }, null)).status, 401);
  assert.equal((await admin(fakeEnv({ ADMIN_KEY: undefined }), '/admin/revoke', {}, 'anything')).status, 503);
  assert.equal(await env.RELAY_KV.get(`revoked:${INSTALL_ID}`), null);
});

test('revoke validates its id', async () => {
  assert.equal((await admin(fakeEnv(), '/admin/revoke', { id: 'not-a-uuid' })).status, 400);
});

test('publish and read catalog, releases (with latest) and findings', async () => {
  const env = fakeEnv();
  assert.equal((await get(env, '/public/catalog')).status, 404);
  await admin(env, '/admin/publish', { what: 'catalog', body: { built: '2026-11', min_cell: 10 } });
  assert.deepEqual(await (await get(env, '/public/catalog')).json(), { built: '2026-11', min_cell: 10 });

  assert.equal((await admin(env, '/admin/publish', { what: 'release', body: {} })).status, 400);
  await admin(env, '/admin/publish', { what: 'release', version: '2026-10', body: { version: '2026-10' } });
  await admin(env, '/admin/publish', { what: 'release', version: '2026-11', body: { version: '2026-11' } });
  assert.equal(await env.RELAY_KV.get('pub:release:latest'), '2026-11');

  const reader = professionalToken({ scopes: ['releases:read'] });
  assert.equal((await get(env, '/public/releases/latest')).status, 401);
  assert.equal((await get(env, '/public/releases/latest', installToken())).status, 403);
  assert.equal((await get(env, '/public/releases/latest', professionalToken())).status, 403);
  assert.equal((await (await get(env, '/public/releases/latest', reader)).json()).version, '2026-11');
  assert.equal((await (await get(env, '/public/releases/2026-10', reader)).json()).version, '2026-10');
  assert.equal((await get(env, '/public/releases/1999-01', reader)).status, 404);

  await admin(env, '/admin/publish', { what: 'findings', body: { items: [] } });
  assert.equal((await get(env, '/public/findings')).status, 401);
  assert.equal((await get(env, '/public/findings', reader)).status, 403);
  assert.deepEqual(await (await get(env, '/public/findings', professionalToken({ scopes: ['findings:read'] }))).json(), { items: [] });
  const open = { ...env, PUBLIC_FINDINGS: '1' };
  assert.equal((await get(open, '/public/findings')).status, 200);

  assert.equal((await admin(env, '/admin/publish', { what: 'other', body: {} })).status, 400);
});

test('the publish body is capped at 5 MiB', async () => {
  const res = await admin(fakeEnv(), '/admin/publish', { what: 'catalog', body: { pad: 'x'.repeat(5 * 1024 * 1024) } });
  assert.equal(res.status, 413);
});
