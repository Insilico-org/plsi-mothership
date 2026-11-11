// Rate limits: fixed one-hour windows in KV, keyed by HMACs, never a raw IP or token id.

import { HOUR, MAX } from './config.js';
import { limited, readJson } from './http.js';
import { hmac, now } from './sign.js';

// IPv6 addresses count per /64 (one subscriber's whole prefix), IPv4 per address.
export function ipBucket(ip) {
  const mapped = ip.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/i);
  if (mapped) return mapped[1];
  if (!ip.includes(':')) return ip;
  const [head, tail] = ip.split('::');
  const h = head ? head.split(':') : [];
  const t = tail ? tail.split(':') : [];
  const groups = tail === undefined ? h : [...h, ...Array(Math.max(0, 8 - h.length - t.length)).fill('0'), ...t];
  return `${groups.slice(0, 4).map((g) => (parseInt(g, 16) || 0).toString(16)).join(':')}::/64`;
}

export async function ipKey(request, env) {
  const ip = ipBucket(request.headers.get('CF-Connecting-IP') || 'unknown');
  return hmac(env.TOKEN_HMAC_SECRET, `ip:${new Date().toISOString().slice(0, 10)}:${ip}`);
}

export const tokenKey = (env, who) => hmac(env.TOKEN_HMAC_SECRET, `rl:${who.id}`);

// The window `rl:<key>` = {count, start} as {key, count, start, ok, retryAfter}; reading it costs no write.
export async function limiter(env, key, limit) {
  const full = `rl:${key}`;
  const t = now();
  let cur = null;
  try { cur = JSON.parse(await env.RELAY_KV.get(full)); } catch { /* unreadable: a fresh window */ }
  const live = cur && Number.isInteger(cur.count) && Number.isInteger(cur.start) && cur.start <= t && t - cur.start < HOUR;
  const count = live ? cur.count : 0;
  const start = live ? cur.start : t;
  return { key: full, count, start, ok: count < limit, retryAfter: Math.max(1, start + HOUR - t) };
}

// Counts one request (false when the KV write fails). Callers hit only once the cheap checks pass (Turnstile, kind),
// so a rejected request costs no KV write.
export async function hit(env, rl) {
  try {
    await env.RELAY_KV.put(rl.key, JSON.stringify({ count: rl.count + 1, start: rl.start }),
      { expiration: Math.max(rl.start + HOUR, now() + 60) }); // KV wants expiration at least 60 s ahead
    return true;
  } catch {
    return false;
  }
}

// The shared start of a form route: its limit is checked, then a small JSON body read: {rl, body} or an error Response.
export async function form(request, env, key, limit) {
  const rl = await limiter(env, key, limit);
  if (!rl.ok) return limited(rl.retryAfter);
  const read = await readJson(request, MAX.form);
  return read instanceof Response ? read : { rl, body: read.body };
}
