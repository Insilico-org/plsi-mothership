// GET /public/*: the published catalog, releases and findings, scope-gated where §4 says so.

import { HEADERS, fail } from './http.js';
import { authenticate } from './tokens.js';

export const PUBLISHED = { catalog: 'pub:catalog', findings: 'pub:findings' };
export const VERSION = /^[0-9A-Za-z._-]{1,64}$/;

async function published(env, key) {
  const value = await env.RELAY_KV.get(key);
  if (value === null) return fail('not_published', 404);
  return new Response(value, { headers: HEADERS });
}

// Null when the request holds a professional token with the scope, else the error Response.
async function requireScope(request, env, scope) {
  const who = await authenticate(request, env);
  if (who instanceof Response) return who;
  return who.type === 'professional' && who.scopes.includes(scope) ? null : fail('missing_scope', 403);
}

export async function publicRead(request, env, path) {
  if (path === '/public/catalog') return published(env, PUBLISHED.catalog);
  if (path === '/public/findings') {
    const denied = env.PUBLIC_FINDINGS === '1' ? null : await requireScope(request, env, 'findings:read');
    return denied || published(env, PUBLISHED.findings);
  }
  const m = path.match(/^\/public\/releases\/([^/]+)$/);
  if (!m) return fail('not_found', 404);
  const denied = await requireScope(request, env, 'releases:read');
  if (denied) return denied;
  const version = m[1] === 'latest' ? await env.RELAY_KV.get('pub:release:latest') : m[1];
  if (version === null) return fail('not_published', 404);
  if (!VERSION.test(version)) return fail('bad_version', 400);
  return published(env, `pub:release:${version}`);
}
