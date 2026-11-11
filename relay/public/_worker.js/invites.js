// Professional invites (WIRE §4): a steward makes a one-time /pro#<code> link; POST /pro/claim spends it for a token.

import { LIMITS, MAX, TTL } from './config.js';
import { fail, json, limited } from './http.js';
import { form, hit, ipKey } from './limits.js';
import { hmac, hmacUuid, now, random } from './sign.js';
import { badGrant, mintProfessional } from './tokens.js';
import { human } from './turnstile.js';

const inviteKey = (id) => `invite:${id}`;
const inviteId = (env, code) => hmac(env.TOKEN_HMAC_SECRET, `inv:${code}`);

async function readInvite(env, id) {
  const raw = await env.RELAY_KV.get(inviteKey(id));
  return raw ? JSON.parse(raw) : null;
}

async function saveInvite(env, id, invite) {
  await env.RELAY_KV.put(inviteKey(id), JSON.stringify(invite),
    { expirationTtl: Math.max(60, invite.exp - now() + TTL.inviteKept) });
}

// POST /admin/invite {role, scopes, days} -> {ok, id, link, exp}.
export async function invite(env, body, request) {
  const grant = { role: body.role, scopes: body.scopes, days: body.days };
  const bad = badGrant(grant);
  if (bad) return fail(bad, 400);
  const code = random(24);
  const id = await inviteId(env, code);
  const exp = now() + TTL.invite;
  await saveInvite(env, id, { ...grant, exp });
  return json({ ok: true, id, link: new URL(`/pro#${code}`, request.url).href, exp });
}

// POST /admin/invite/status {ids} -> {ok, invites: {id: {status: pending|claimed|expired|unknown, pid?, claimed_at?}}}.
export async function inviteStatus(env, body) {
  const { ids } = body;
  if (!Array.isArray(ids) || ids.length > MAX.inviteIds
      || !ids.every((i) => typeof i === 'string' && /^[\w-]{20,64}$/.test(i))) return fail('bad_ids', 400);
  const invites = {};
  for (const id of ids) {
    const inv = await readInvite(env, id);
    invites[id] = !inv ? { status: 'unknown' }
      : inv.pid ? { status: 'claimed', pid: inv.pid, claimed_at: inv.claimed_at }
        : { status: inv.exp <= now() ? 'expired' : 'pending' };
  }
  return json({ ok: true, invites });
}

// POST /pro/claim {invite, turnstileToken} -> {ok, pid, role, scopes, exp, token}, once per invite.
export async function claim(request, env) {
  if (!env.TOKEN_HMAC_SECRET || !env.TURNSTILE_SECRET_KEY) return fail('pro_not_configured', 503);
  const read = await form(request, env, `pro:${await ipKey(request, env)}`, LIMITS.pro);
  if (read instanceof Response) return read;
  const code = typeof read.body.invite === 'string' ? read.body.invite.trim() : '';
  if (!code || code.length > 100) return fail('invalid_invite', 400);
  const denied = await human(request, env, read.body, 'pro');
  if (denied) return denied;
  if (!(await hit(env, read.rl))) return limited();
  const id = await inviteId(env, code);
  const inv = await readInvite(env, id);
  if (!inv || inv.exp <= now()) return fail('invalid_or_expired_invite', 400);
  if (inv.pid) return fail('invite_already_used', 400);
  // The pid derives from the invite, so two claims racing past the KV check share one pid and one revoke stops both.
  const minted = await mintProfessional(env, inv, await hmacUuid(env.TOKEN_HMAC_SECRET, `pid:${inviteKey(id)}`));
  await saveInvite(env, id, { ...inv, pid: minted.pid, claimed_at: now() });
  return json({ ok: true, pid: minted.pid, role: inv.role, scopes: inv.scopes, exp: minted.exp, token: minted.token });
}
