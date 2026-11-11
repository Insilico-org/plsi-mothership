// The Mother's ACK under a sender's ticket (WIRE §4): /ack reads it; /admin/ack writes it; /admin/body takes a body.

import { LIMITS, MAX, TTL } from './config.js';
import { fail, json, limited } from './http.js';
import { form, hit, tokenKey } from './limits.js';
import { enc } from './sign.js';
import { authenticate, isUuid, revoke } from './tokens.js';

export async function ack(request, env) {
  const who = await authenticate(request, env);
  if (who instanceof Response) return who;
  const read = await form(request, env, `ack:${await tokenKey(env, who)}`, LIMITS.ack);
  if (read instanceof Response) return read;
  if (!(await hit(env, read.rl))) return limited();
  const { ticket } = read.body;
  if (!isUuid(ticket)) return fail('bad_ticket', 400);
  const value = await env.RELAY_KV.get(`ack:${ticket}`);
  if (value === null) return json({ ok: true, status: 'pending' });
  const theAck = JSON.parse(value);
  // The install that asked for a deletion is retired once it reads the confirmation, so a stray token copy cannot send.
  if (theAck.ok && theAck.status === 'deleted' && (await env.RELAY_KV.get(`deleting:${ticket}`)) === who.id) {
    await revoke(env, who.id);
    await env.RELAY_KV.delete(`deleting:${ticket}`);
  }
  return json({ ok: true, status: 'done', ack: theAck });
}

// POST /admin/ack {ref, ack} and POST /admin/body {ref}; admin.js has checked the ref.
export async function postAck(env, body) {
  if (!body.ack || typeof body.ack !== 'object' || Array.isArray(body.ack)) return fail('bad_ack', 400);
  const value = JSON.stringify(body.ack);
  if (enc.encode(value).byteLength > MAX.ack) return fail('ack_too_large', 413);
  await env.RELAY_KV.put(`ack:${body.ref}`, value, { expirationTtl: TTL.ack });
  return json({ ok: true });
}

export async function takeBody(env, body) {
  const key = `body:${body.ref}`;
  const value = await env.RELAY_KV.get(key);
  if (value === null) return fail('ref_not_found', 404);
  await env.RELAY_KV.delete(key);
  return json({ ok: true, ...JSON.parse(value) });
}
