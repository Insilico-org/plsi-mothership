// Install and professional tokens (§2): minting, reading and checking a request's bearer token.

import { b64url, enc, hmac, now, same, unb64url } from './sign.js';
import { bearer, fail } from './http.js';

export const ROLES = ['physician', 'researcher'];
export const SCOPES = ['releases:read', 'findings:read', 'findings:submit'];
export const MAX_DAYS = 366;
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function mintInstall(env) {
  const id = crypto.randomUUID();
  return { install_id: id, token: `plsi1.${b64url(enc.encode(id))}.${await hmac(env.TOKEN_HMAC_SECRET, `v1:${id}`)}` };
}

// The error name for a bad {role, scopes, days} grant, or null.
export function badGrant({ role, scopes, days }) {
  if (!ROLES.includes(role)) return 'bad_role';
  if (!Array.isArray(scopes) || !scopes.length || !scopes.every((s) => SCOPES.includes(s))
      || new Set(scopes).size !== scopes.length) return 'bad_scopes';
  if (!Number.isInteger(days) || days < 1 || days > MAX_DAYS) return 'bad_days';
  return null;
}

export async function mintProfessional(env, { role, scopes, days }, pid) {
  const exp = now() + days * 86400;
  const payload = b64url(enc.encode(JSON.stringify({ pid, role, scopes, exp })));
  return { pid, exp, token: `plsp1.${payload}.${await hmac(env.TOKEN_HMAC_SECRET, `v1p:${payload}`)}` };
}

// The signed identity of a bearer token, or null: {type: 'install', id} or {type: 'professional', id, role, scopes, exp}.
async function readToken(env, token) {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    if (parts[0] === 'plsi1') {
      const id = unb64url(parts[1]);
      if (!UUID.test(id) || !same(parts[2], await hmac(env.TOKEN_HMAC_SECRET, `v1:${id}`))) return null;
      return { type: 'install', id };
    }
    if (parts[0] === 'plsp1') {
      if (!same(parts[2], await hmac(env.TOKEN_HMAC_SECRET, `v1p:${parts[1]}`))) return null;
      const p = JSON.parse(unb64url(parts[1]));
      if (!UUID.test(p.pid) || !ROLES.includes(p.role) || !Array.isArray(p.scopes) || !Number.isFinite(p.exp)) return null;
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
