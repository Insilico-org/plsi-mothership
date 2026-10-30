// POST /relay (§3, §4): check the token and that the body's kind is allowed for it, park the body with its sender and
// forward a relay_ref. Grok Bot shows a webhook wake only its first ~4 KiB of body and answers the caller with its own
// run id, so every body waits in KV for the Mother to take (admin /admin/body) and the ref doubles as the sender's
// ticket for the Mother's ACK (ack.js). A parked body lives at most PARK_TTL seconds; neither it nor the ACK names the
// install token.

import { sha256Hex } from './sign.js';
import { fail, json, limited, readJson } from './http.js';
import { HOUR, limiter, tokenKey } from './limits.js';
import { authenticate } from './tokens.js';

export const SCHEMA_VERSION = 4; // of the relay_ref the relay itself writes
const RELAY_MAX = 256 * 1024;
export const PARK_TTL = 86400;
// Messages per token per hour, and webhook forwards per hour across all tokens: each forward wakes the Grok bot, so
// this caps what any number of tokens can cost. The global count is approximate (concurrent hits can be lost).
const PER_HOUR = { install: 10, professional: 30 };
const FORWARDS_PER_HOUR = 200;
// §3: kind -> who may send it (install, or the professional scope it needs; pid: body carries professional_id).
// plsi_research_note is not here: research notes travel only by email (§7).
const KINDS = {
  plsi_contribution: { install: true },
  plsi_withdrawal: { install: true },
  plsi_registry_update: { install: true },
  plsi_preregistration: { scope: 'findings:submit', pid: true },
  plsi_finding: { scope: 'findings:submit', pid: true },
};

export async function relay(request, env) {
  const who = await authenticate(request, env);
  if (who instanceof Response) return who;
  const mine = await limiter(env, `relay:${await tokenKey(env, who)}`, PER_HOUR[who.type], HOUR);
  if (!mine.ok) return limited(mine.retryAfter);
  if (!env.MOTHERSHIP_WEBHOOK_URL || !env.MOTHERSHIP_SENDER_KEY) return fail('relay_not_configured', 503);

  const read = await readJson(request, RELAY_MAX);
  if (read instanceof Response) return read;
  const msg = read.body;
  const rule = Object.hasOwn(KINDS, msg.kind) ? KINDS[msg.kind] : null;
  const allowed = rule && (who.type === 'install' ? rule.install : rule.scope && who.scopes.includes(rule.scope));
  if (!allowed) return fail('kind_not_allowed', 403);
  if (rule.pid && msg.professional_id !== who.id) return fail('professional_id_mismatch', 403);
  const all = await limiter(env, 'forward', FORWARDS_PER_HOUR, HOUR);
  if (!all.ok) return limited(all.retryAfter);
  if (!(await mine.hit())) return limited();

  // The parked record names the sender type (and a professional's pid and role) for the Mother, so nothing it trusts
  // rides on webhook headers; the webhook carries only the relay's key and the ref.
  const parked = { body: new TextDecoder().decode(read.raw), sender: who.type === 'install' ? 'contributor' : 'professional' };
  if (who.type === 'professional') Object.assign(parked, { professional_id: who.id, role: who.role });
  const ref = crypto.randomUUID();
  const key = `body:${ref}`;
  try {
    await env.RELAY_KV.put(key, JSON.stringify(parked), { expirationTtl: PARK_TTL });
  } catch {
    return fail('relay_unavailable', 503);
  }
  await all.hit(); // a lost global hit only makes the cap softer
  const drop = () => env.RELAY_KV.delete(key).catch(() => {});
  const forward = JSON.stringify({ kind: 'relay_ref', schema_version: SCHEMA_VERSION, ref,
    sha256: await sha256Hex(read.raw), bytes: read.raw.byteLength });
  let upstream;
  try {
    upstream = await fetch(env.MOTHERSHIP_WEBHOOK_URL, { method: 'POST', body: forward, headers: {
      'content-type': 'application/json', authorization: `Bearer ${env.MOTHERSHIP_SENDER_KEY}` } });
  } catch {
    await drop();
    return fail('upstream_unavailable', 503);
  }
  // Nothing of the webhook's reply reaches the sender (no status, no text): only whether it took the ref.
  if (upstream.body) await upstream.body.cancel().catch(() => {});
  if (!upstream.ok) {
    await drop();
    return fail('upstream_error', 502);
  }
  return json({ ok: true, ticket: ref }, 202);
}
