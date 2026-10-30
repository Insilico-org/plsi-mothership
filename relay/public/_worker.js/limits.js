// Rate limits (§8): fixed windows in KV, keyed by HMACs, never a raw IP or token id.

import { hmac, now } from './sign.js';

export const HOUR = 3600;

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

// A fixed-window counter `rl:<key>` = {count, start}: {ok} says whether one more is allowed, retryAfter is the whole
// seconds (at least 1) until the window ends, hit() counts it (false when the KV write fails). The record expires at the window's end, so a hit never extends the window. Callers check
// first and hit only once a request has passed its cheap checks (Turnstile, kind), so rejected requests cost no write.
// Best-effort: KV is not atomic and eventually consistent, so concurrent hits can be lost and quotas are soft.
export async function limiter(env, key, limit, windowSec) {
  const full = `rl:${key}`;
  const t = now();
  let cur = null;
  try { cur = JSON.parse(await env.RELAY_KV.get(full)); } catch { /* unreadable: a fresh window */ }
  const live = cur && Number.isInteger(cur.count) && Number.isInteger(cur.start) && cur.start <= t && t - cur.start < windowSec;
  const count = live ? cur.count : 0;
  const start = live ? cur.start : t;
  return {
    ok: count < limit,
    retryAfter: Math.max(1, start + windowSec - t),
    async hit() {
      try {
        await env.RELAY_KV.put(full, JSON.stringify({ count: count + 1, start }),
          { expiration: Math.max(start + windowSec, now() + 60) }); // KV wants expiration at least 60 s ahead
        return true;
      } catch {
        return false;
      }
    },
  };
}
