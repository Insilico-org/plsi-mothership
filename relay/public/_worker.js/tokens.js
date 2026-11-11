// Install and professional tokens (WIRE §2): minting, reading, checking and revoking.

import { DAY, MAX } from './config.js';
import { bearer, fail } from './http.js';
import { b64url, enc, hmac, now, same, unb64url } from './sign.js';

const ROLES = ['physician', 'researcher'];
const SCOPES = ['releases:read', 'findings:read', 'findings:submit'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const isUuid = (s) => typeof s === 'string' && UUID.test(s);
export const revoke = (env, id) => env.RELAY_KV.put(`revoked:${id}`, '1');
const installSig = (env, id) => hmac(env.TOKEN_HMAC_SECRET, `v1:${id}`);
const proSig = (env, payload) => hmac(env.TOKEN_HMAC_SECRET, `v1p:${payload}`);

export async function mintInstall(env) {
  const id = crypto.randomUUID();
  return { install_id: id, token: `plsi1.${b64url(enc.encode(id))}.${await installSig(env, id)}` };
}

// The error name for a bad {role, scopes, days} grant, or null.
export function badGrant({ role, scopes, days }) {
  if (!ROLES.includes(role)) return 'bad_role';
  if (!Array.isArray(scopes) || !scopes.length || !scopes.every((s) => SCOPES.includes(s))
      || new Set(scopes).size !== scopes.length) return 'bad_scopes';
  if (!Number.isInteger(days) || days < 1 || days > MAX.tokenDays) return 'bad_days';
  return null;
}

export async function mintProfessional(env, { role, scopes, days }, pid) {
  const exp = now() + days * DAY;
  const payload = b64url(enc.encode(JSON.stringify({ pid, role, scopes, exp })));
  return { pid, exp, token: `plsp1.${payload}.${await proSig(env, payload)}` };
}

// The signed identity of a bearer token, or null: {type: 'install', id} or {type: 'professional', id, role, scopes, exp}.
async function readToken(env, token) {
  const [tag, payload, sig, ...rest] = token.split('.');
  if (rest.length || sig === undefined) return null;
  try {
    if (tag === 'plsi1') {
      const id = unb64url(payload);
      return isUuid(id) && same(sig, await installSig(env, id)) ? { type: 'install', id } : null;
    }
    if (tag === 'plsp1' && same(sig, await proSig(env, payload))) {
      const p = JSON.parse(unb64url(payload));
      if (!isUuid(p.pid) || !ROLES.includes(p.role) || !Array.isArray(p.scopes) || !Number.isFinite(p.exp)) return null;
      return { type: 'professional', id: p.pid, role: p.role, scopes: p.scopes, exp: p.exp };
    }
  } catch { /* malformed token */ }
  return null;
}

// The verified, unrevoked, unexpired token of the request, or an error Response. A missing secret is the relay's
// fault (503), never the token's (401 would tell every client to re-join).
export async function authenticate(request, env) {
  if (!env.TOKEN_HMAC_SECRET) return fail('relay_not_configured', 503);
  const who = await readToken(env, bearer(request));
  if (!who) return fail('unauthorized', 401);
  if (await env.RELAY_KV.get(`revoked:${who.id}`)) return fail('token_revoked', 401);
  if (who.type === 'professional' && who.exp <= now()) return fail('token_expired', 401);
  return who;
}
