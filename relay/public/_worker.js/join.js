// Contributors: /join/issue hands out a one-time enroll code after Turnstile; /enroll spends it for an install token.

import { hmac, now, random, same } from './sign.js';
import { FORM_MAX, fail, json, limited, readJson } from './http.js';
import { HOUR, ipKey, limiter } from './limits.js';
import { mintInstall } from './tokens.js';
import { human } from './turnstile.js';

const CODE_TTL = 900;

async function mintEnrollCode(env) {
  const nonce = random(18);
  const exp = now() + CODE_TTL;
  return `e1.${nonce}.${exp}.${await hmac(env.TOKEN_HMAC_SECRET, `e1:${nonce}:${exp}`)}`;
}

// Spends a one-time code: null when good, else the error name.
async function spendEnrollCode(env, code) {
  const [tag, nonce, expText, sig, ...rest] = code.split('.');
  const exp = parseInt(expText, 10);
  if (tag !== 'e1' || !nonce || !sig || rest.length || !Number.isFinite(exp) || exp < now()) return 'invalid_or_expired_code';
  if (!same(sig, await hmac(env.TOKEN_HMAC_SECRET, `e1:${nonce}:${exp}`))) return 'invalid_or_expired_code';
  if (await env.RELAY_KV.get(`used:${nonce}`)) return 'code_already_used';
  await env.RELAY_KV.put(`used:${nonce}`, '1', { expirationTtl: Math.max(60, exp - now()) });
  return null;
}

export async function joinIssue(request, env) {
  if (!env.TOKEN_HMAC_SECRET || !env.TURNSTILE_SECRET_KEY) return fail('join_not_configured', 503);
  const rl = await limiter(env, `join:${await ipKey(request, env)}`, 20, HOUR);
  if (!rl.ok) return limited(rl.retryAfter);
  const read = await readJson(request, FORM_MAX);
  if (read instanceof Response) return read;
  const denied = await human(request, env, read.body, 'join');
  if (denied) return denied;
  if (!(await rl.hit())) return limited();
  return json({ ok: true, code: await mintEnrollCode(env), expires_in_sec: CODE_TTL });
}

export async function enroll(request, env) {
  if (!env.TOKEN_HMAC_SECRET) return fail('enroll_not_configured', 503);
  const rl = await limiter(env, `enroll:${await ipKey(request, env)}`, 40, HOUR);
  if (!rl.ok) return limited(rl.retryAfter);
  const read = await readJson(request, FORM_MAX);
  if (read instanceof Response) return read;
  const code = typeof read.body.code === 'string' ? read.body.code.trim() : '';
  if (!code || code.length > 200) return fail('invalid_code', 400);
  if (!(await rl.hit())) return limited();
  const error = await spendEnrollCode(env, code);
  if (error) return fail(error, 400);
  return json({ ok: true, ...(await mintInstall(env)) });
}
