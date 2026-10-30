// Professional invites (§4): a steward's ADMIN_KEY call makes a one-time link that the Mother emails to the verified
// address; the person opens /pro#<code>, passes Turnstile and gets the token once. Opening the link spends nothing
// (mail scanners open every link): only POST /pro/claim does. The relay stores no email, only an HMAC of the code.
// The pid is derived from the invite, so if two claims race past the KV check, both tokens share one pid and one
// revoke stops both.

import { hmac, hmacUuid, now, random } from './sign.js';
import { FORM_MAX, fail, json, limited, readJson } from './http.js';
import { HOUR, ipKey, limiter } from './limits.js';
import { badGrant, mintProfessional } from './tokens.js';
import { human } from './turnstile.js';

export const INVITE_TTL = 72 * 3600;
const KEEP = 90 * 86400; // a spent or expired invite stays readable by status for this long
const STATUS_MAX = 100;

const inviteKey = async (env, code) => `invite:${await hmac(env.TOKEN_HMAC_SECRET, `inv:${code}`)}`;

async function put(env, key, invite) {
  await env.RELAY_KV.put(key, JSON.stringify(invite), { expirationTtl: Math.max(60, invite.exp - now() + KEEP) });
}

// POST /admin/invite {role, scopes, days} -> {ok, id, link, exp}.
export async function invite(request, env, body) {
  const grant = { role: body.role, scopes: body.scopes, days: body.days };
  const bad = badGrant(grant);
  if (bad) return fail(bad, 400);
  const code = random(24);
  const key = await inviteKey(env, code);
  const exp = now() + INVITE_TTL;
  await put(env, key, { ...grant, exp });
  return json({ ok: true, id: key.slice('invite:'.length), link: new URL(`/pro#${code}`, request.url).href, exp });
}

// POST /admin/invite/status {ids} -> {ok, invites: {id: {status: pending|claimed|expired|unknown, pid?, claimed_at?}}}.
export async function inviteStatus(env, body) {
  const { ids } = body;
  if (!Array.isArray(ids) || ids.length > STATUS_MAX || !ids.every((i) => typeof i === 'string' && /^[\w-]{20,64}$/.test(i))) {
    return fail('bad_ids', 400);
  }
  const invites = {};
  for (const id of ids) {
    const raw = await env.RELAY_KV.get(`invite:${id}`);
    const inv = raw ? JSON.parse(raw) : null;
    invites[id] = !inv ? { status: 'unknown' }
      : inv.pid ? { status: 'claimed', pid: inv.pid, claimed_at: inv.claimed_at }
        : { status: inv.exp <= now() ? 'expired' : 'pending' };
  }
  return json({ ok: true, invites });
}

// POST /pro/claim {invite, turnstileToken} -> {ok, pid, role, scopes, exp, token}, once per invite.
export async function claim(request, env) {
  if (!env.TOKEN_HMAC_SECRET || !env.TURNSTILE_SECRET_KEY) return fail('pro_not_configured', 503);
  const rl = await limiter(env, `pro:${await ipKey(request, env)}`, 20, HOUR);
  if (!rl.ok) return limited(rl.retryAfter);
  const read = await readJson(request, FORM_MAX);
  if (read instanceof Response) return read;
  const code = typeof read.body.invite === 'string' ? read.body.invite.trim() : '';
  if (!code || code.length > 100) return fail('invalid_invite', 400);
  const denied = await human(request, env, read.body, 'pro');
  if (denied) return denied;
  if (!(await rl.hit())) return limited();
  const key = await inviteKey(env, code);
  const raw = await env.RELAY_KV.get(key);
  const inv = raw ? JSON.parse(raw) : null;
  if (!inv || inv.exp <= now()) return fail('invalid_or_expired_invite', 400);
  if (inv.pid) return fail('invite_already_used', 400);
  const minted = await mintProfessional(env, inv, await hmacUuid(env.TOKEN_HMAC_SECRET, `pid:${key}`));
  await put(env, key, { ...inv, pid: minted.pid, claimed_at: now() });
  return json({ ok: true, pid: minted.pid, role: inv.role, scopes: inv.scopes, exp: minted.exp, token: minted.token });
}
