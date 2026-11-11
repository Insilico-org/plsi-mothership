// Contributors: /join/issue hands out a one-time enroll code after Turnstile; /enroll spends it for an install token.

import { LIMITS, TTL } from './config.js';
import { fail, json, limited } from './http.js';
import { form, hit, ipKey } from './limits.js';
import { hmac, now, random, same } from './sign.js';
import { mintInstall } from './tokens.js';
import { human } from './turnstile.js';

const codeSig = (env, nonce, exp) => hmac(env.TOKEN_HMAC_SECRET, `e1:${nonce}:${exp}`);

async function mintEnrollCode(env) {
  const nonce = random(18);
  const exp = now() + TTL.code;
  return `e1.${nonce}.${exp}.${await codeSig(env, nonce, exp)}`;
}

// Spends a one-time code: null when good, else the error name.
async function spendEnrollCode(env, code) {
  const [tag, nonce, expText, sig, ...rest] = code.split('.');
  const exp = parseInt(expText, 10);
  if (tag !== 'e1' || !nonce || !sig || rest.length || !Number.isFinite(exp) || exp < now()) return 'invalid_or_expired_code';
  if (!same(sig, await codeSig(env, nonce, exp))) return 'invalid_or_expired_code';
  if (await env.RELAY_KV.get(`used:${nonce}`)) return 'code_already_used';
  await env.RELAY_KV.put(`used:${nonce}`, '1', { expirationTtl: Math.max(60, exp - now()) });
  return null;
}

export async function joinIssue(request, env) {
  if (!env.TOKEN_HMAC_SECRET || !env.TURNSTILE_SECRET_KEY) return fail('join_not_configured', 503);
  const read = await form(request, env, `join:${await ipKey(request, env)}`, LIMITS.join);
  if (read instanceof Response) return read;
  const denied = await human(request, env, read.body, 'join');
  if (denied) return denied;
  if (!(await hit(env, read.rl))) return limited();
  return json({ ok: true, code: await mintEnrollCode(env), expires_in_sec: TTL.code });
}

export async function enroll(request, env) {
  if (!env.TOKEN_HMAC_SECRET) return fail('enroll_not_configured', 503);
  const read = await form(request, env, `enroll:${await ipKey(request, env)}`, LIMITS.enroll);
  if (read instanceof Response) return read;
  const code = typeof read.body.code === 'string' ? read.body.code.trim() : '';
  if (!code || code.length > 200) return fail('invalid_code', 400);
  if (!(await hit(env, read.rl))) return limited();
  const error = await spendEnrollCode(env, code);
  if (error) return fail(error, 400);
  return json({ ok: true, ...(await mintInstall(env)) });
}
