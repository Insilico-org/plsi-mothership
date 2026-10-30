// Fake Pages env and upstreams for the relay tests: in-memory KV, static assets from public/, string secrets, stubbed fetch.
// RELAY_WORKER=upload runs the same tests against the `npm run upload` bundle.
import { createHmac } from 'node:crypto';
import { readFile } from 'node:fs/promises';

const { default: worker } = await import(process.env.RELAY_WORKER === 'upload' ? '../upload/_worker.js' : '../public/_worker.js/index.js');

export const SECRET = 'test-hmac-secret';
export const ADMIN = 'test-admin-key';
export const INTAKE = 'test-intake-key';
export const SENDER = 'test-sender-key';
export const WEBHOOK = 'https://mother.test/intake';
export const IP = '203.0.113.7';

export function fakeKv() {
  const data = new Map();
  return {
    data,
    async get(key) {
      const e = data.get(key);
      return e && !(e.expiration <= Date.now() / 1000) ? e.value : null;
    },
    async put(key, value, opts = {}) {
      data.set(key, { value: String(value), ttl: opts.expirationTtl, expiration: opts.expiration });
    },
    async delete(key) { data.delete(key); },
  };
}

// Serves public/<file>, and public/<path>.html like Pages pretty URLs, else 404.
export function fakeAssets() {
  return {
    async fetch(request) {
      const name = new URL(request.url).pathname.replace(/^\/+/, '') || 'index';
      const file = name.includes('.') ? name : `${name}.html`;
      const type = file.endsWith('.css') ? 'text/css' : 'text/html; charset=utf-8';
      try {
        return new Response(await readFile(new URL(`../public/${file}`, import.meta.url), 'utf-8'), { headers: { 'content-type': type } });
      } catch {
        return new Response('not found', { status: 404 });
      }
    },
  };
}

export function fakeEnv(extra = {}) {
  return {
    RELAY_KV: fakeKv(),
    ASSETS: fakeAssets(),
    TOKEN_HMAC_SECRET: SECRET,
    MOTHERSHIP_WEBHOOK_URL: WEBHOOK,
    MOTHERSHIP_SENDER_KEY: SENDER,
    TURNSTILE_SITE_KEY: 'site-key-1',
    TURNSTILE_SECRET_KEY: 'turnstile-secret',
    ADMIN_KEY: ADMIN,
    INTAKE_KEY: INTAKE,
    ENROLLMENT_OPEN: '1',
    ...extra,
  };
}

// Stubs globalThis.fetch. A Turnstile token "<action>[@<hostname>]" passes for that action and hostname (default
// relay.test), "bad" fails. The Mother answers mother.reply() (default 202 with a receipt).
export const calls = [];
export const mother = { reply: null };
const receipt = () => new Response(JSON.stringify({ ok: true, receipt: 'r_0123456789abcdef' }), {
  status: 202, headers: { 'content-type': 'application/json' },
});
globalThis.fetch = async (url, init = {}) => {
  calls.push({ url: String(url), init });
  if (String(url).includes('siteverify')) {
    const token = init.body.get('response');
    const [action, hostname = 'relay.test'] = token.split('@');
    return Response.json({ success: token !== 'bad', action, hostname });
  }
  return (mother.reply || receipt)();
};

export function call(env, method, path, { body, token, ip = IP, headers: extra = {} } = {}) {
  const headers = { 'CF-Connecting-IP': ip, ...extra };
  if (token) headers.authorization = `Bearer ${token}`;
  if (body !== undefined) headers['content-type'] = 'application/json';
  const payload = body === undefined || typeof body === 'string' || body instanceof ReadableStream ? body : JSON.stringify(body);
  const init = { method, headers, body: payload };
  if (payload instanceof ReadableStream) init.duplex = 'half';
  return worker.fetch(new Request(`https://relay.test${path}`, init), env);
}

// A body stream that never ends: 1 KiB chunks forever, counting how many were pulled.
export function endless() {
  const pulled = { n: 0 };
  const stream = new ReadableStream({ pull(c) { pulled.n += 1; c.enqueue(new Uint8Array(1024).fill(32)); } });
  return { stream, pulled };
}

export const b64url = (s) => Buffer.from(s).toString('base64url');
export const sign = (msg) => createHmac('sha256', SECRET).update(msg).digest('base64url');
export const INSTALL_ID = '11111111-2222-4333-8444-555555555555';
export const PID = '99999999-2222-4333-8444-555555555555';

export function installToken(id = INSTALL_ID) {
  return `plsi1.${b64url(id)}.${sign(`v1:${id}`)}`;
}

export function professionalToken({ pid = PID, role = 'researcher', scopes = [], exp } = {}) {
  const body = b64url(JSON.stringify({ pid, role, scopes, exp: exp ?? Math.floor(Date.now() / 1000) + 3600 }));
  return `plsp1.${body}.${sign(`v1p:${body}`)}`;
}

export function enrollCode(exp = Math.floor(Date.now() / 1000) + 900) {
  const nonce = Math.random().toString(36).slice(2);
  return `e1.${nonce}.${exp}.${sign(`e1:${nonce}:${exp}`)}`;
}
