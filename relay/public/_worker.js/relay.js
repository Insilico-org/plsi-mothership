// POST /relay (WIRE §3, §4): check the token and kind, park the body with its sender, forward a relay_ref.

import { LIMITS, MAX, TTL } from './config.js';
import { fail, json, limited, readJson } from './http.js';
import { hit, limiter, tokenKey } from './limits.js';
import { sha256Hex } from './sign.js';
import { authenticate } from './tokens.js';

const SCHEMA_VERSION = 4; // of the relay_ref the relay itself writes
// kind -> who may send it: install, or the professional scope it needs (pid: the body carries professional_id).
const KINDS = {
  plsi_contribution: { install: true },
  plsi_withdrawal: { install: true },
  plsi_registry_update: { install: true },
  plsi_preregistration: { scope: 'findings:submit', pid: true },
  plsi_finding: { scope: 'findings:submit', pid: true },
};

// Wakes the Mother with the ref: null when the webhook took it, else the error Response (none of its reply leaks).
async function forward(env, ref, raw) {
  const body = JSON.stringify({ kind: 'relay_ref', schema_version: SCHEMA_VERSION, ref,
    sha256: await sha256Hex(raw), bytes: raw.byteLength });
  try {
    const res = await fetch(env.MOTHERSHIP_WEBHOOK_URL, { method: 'POST', body, headers: {
      'content-type': 'application/json', authorization: `Bearer ${env.MOTHERSHIP_SENDER_KEY}` } });
    if (res.body) await res.body.cancel().catch(() => {});
    return res.ok ? null : fail('upstream_error', 502);
  } catch {
    return fail('upstream_unavailable', 503);
  }
}

async function drop(env, ref) {
  for (const key of [`body:${ref}`, `deleting:${ref}`]) await env.RELAY_KV.delete(key).catch(() => {});
}

export async function relay(request, env) {
  const who = await authenticate(request, env);
  if (who instanceof Response) return who;
  const mine = await limiter(env, `relay:${await tokenKey(env, who)}`, LIMITS[who.type]);
  if (!mine.ok) return limited(mine.retryAfter);
  if (!env.MOTHERSHIP_WEBHOOK_URL || !env.MOTHERSHIP_SENDER_KEY) return fail('relay_not_configured', 503);

  const read = await readJson(request, MAX.relay);
  if (read instanceof Response) return read;
  const msg = read.body;
  const rule = Object.hasOwn(KINDS, msg.kind) ? KINDS[msg.kind] : null;
  const allowed = rule && (who.type === 'install' ? rule.install : rule.scope && who.scopes.includes(rule.scope));
  if (!allowed) return fail('kind_not_allowed', 403);
  if (rule.pid && msg.professional_id !== who.id) return fail('professional_id_mismatch', 403);
  const all = await limiter(env, 'forward', LIMITS.forward);
  if (!all.ok) return limited(all.retryAfter);
  if (!(await hit(env, mine))) return limited();

  // Grok Bot shows a webhook wake only ~4 KiB of body, so every body waits in KV with its sender for the Mother to
  // take; a deletion also keeps its install id under the ref until that install reads the `deleted` ACK (ack.js).
  const parked = { body: read.text, sender: who.type === 'install' ? 'contributor' : 'professional' };
  if (who.type === 'professional') Object.assign(parked, { professional_id: who.id, role: who.role });
  const ref = crypto.randomUUID();
  try {
    await env.RELAY_KV.put(`body:${ref}`, JSON.stringify(parked), { expirationTtl: TTL.park });
    if (who.type === 'install' && msg.kind === 'plsi_withdrawal' && msg.request === 'delete_all') {
      await env.RELAY_KV.put(`deleting:${ref}`, who.id, { expirationTtl: TTL.ack });
    }
  } catch {
    await drop(env, ref);
    return fail('relay_unavailable', 503);
  }
  await hit(env, all); // a lost global hit only makes the cap softer
  const error = await forward(env, ref, read.raw);
  if (!error) return json({ ok: true, ticket: ref }, 202);
  await drop(env, ref);
  return error;
}
