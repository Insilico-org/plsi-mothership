// The Mother's ACK for a sender's ticket (§4): POST /ack reads it with any valid token; POST /admin/ack (INTAKE_KEY)
// writes it and POST /admin/body hands the Mother a parked body and its sender once. Keys are the ref only, never the token.

import { FORM_MAX, fail, json, limited, readJson } from './http.js';
import { HOUR, limiter, tokenKey } from './limits.js';
import { UUID, authenticate } from './tokens.js';

const ACK_TTL = 7 * 86400;
const ACK_MAX = 4 * 1024;
const ACK_READS = 60; // per token per hour

export async function ack(request, env) {
  const who = await authenticate(request, env);
  if (who instanceof Response) return who;
  const rl = await limiter(env, `ack:${await tokenKey(env, who)}`, ACK_READS, HOUR);
  if (!rl.ok) return limited(rl.retryAfter);
  if (!(await rl.hit())) return limited();
  const read = await readJson(request, FORM_MAX);
  if (read instanceof Response) return read;
  const ticket = read.body.ticket;
  if (typeof ticket !== 'string' || !UUID.test(ticket)) return fail('bad_ticket', 400);
  const value = await env.RELAY_KV.get(`ack:${ticket}`);
  if (value === null) return json({ ok: true, status: 'pending' });
  return json({ ok: true, status: 'done', ack: JSON.parse(value) });
}

export async function postAck(env, body) {
  if (typeof body.ref !== 'string' || !UUID.test(body.ref)) return fail('bad_ref', 400);
  if (!body.ack || typeof body.ack !== 'object' || Array.isArray(body.ack)) return fail('bad_ack', 400);
  const value = JSON.stringify(body.ack);
  if (new TextEncoder().encode(value).byteLength > ACK_MAX) return fail('ack_too_large', 413);
  await env.RELAY_KV.put(`ack:${body.ref}`, value, { expirationTtl: ACK_TTL });
  return json({ ok: true });
}

export async function takeBody(env, body) {
  if (typeof body.ref !== 'string' || !UUID.test(body.ref)) return fail('bad_ref', 400);
  const key = `body:${body.ref}`;
  const value = await env.RELAY_KV.get(key);
  if (value === null) return fail('ref_not_found', 404);
  await env.RELAY_KV.delete(key);
  return json({ ok: true, ...JSON.parse(value) });
}
