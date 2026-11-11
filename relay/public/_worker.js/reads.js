// GET /public/* and their writer POST /admin/publish: the catalog, releases and findings, scope-gated per WIRE §4.

import { fail, HEADERS, json } from './http.js';
import { authenticate } from './tokens.js';

const PUBLISHED = { catalog: 'pub:catalog', findings: 'pub:findings' };
const VERSION = /^[0-9A-Za-z._-]{1,64}$/;
const releaseKey = (version) => `pub:release:${version}`;

async function published(env, key) {
  const value = await env.RELAY_KV.get(key);
  return value === null ? fail('not_published', 404) : new Response(value, { headers: HEADERS });
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
  const version = m[1] === 'latest' ? await env.RELAY_KV.get(releaseKey('latest')) : m[1];
  if (version === null) return fail('not_published', 404);
  if (!VERSION.test(version)) return fail('bad_version', 400);
  return published(env, releaseKey(version));
}

// POST /admin/publish {what: catalog|release|findings, version?, body}; a release also moves `latest`.
export async function publish(env, body) {
  const { what, version } = body;
  if (!body.body || typeof body.body !== 'object') return fail('bad_body', 400);
  const value = JSON.stringify(body.body);
  if (what === 'release') {
    if (typeof version !== 'string' || !VERSION.test(version) || version === 'latest') return fail('bad_version', 400);
    await env.RELAY_KV.put(releaseKey(version), value);
    await env.RELAY_KV.put(releaseKey('latest'), version);
  } else if (Object.hasOwn(PUBLISHED, what)) {
    await env.RELAY_KV.put(PUBLISHED[what], value);
  } else {
    return fail('bad_what', 400);
  }
  return json({ ok: true });
}
