// PLSI <-> Longevity Mothership relay (WIRE §2-§4), Pages advanced mode entry; it never logs bodies, tokens or IPs.

import { ack } from './ack.js';
import { admin } from './admin.js';
import { fail, secured } from './http.js';
import { claim } from './invites.js';
import { enroll, joinIssue } from './join.js';
import { publicRead } from './reads.js';
import { relay } from './relay.js';
import { closedPage, page } from './turnstile.js';

// path (or '/<prefix>/' for all under it) -> {method, handler, kv: needs RELAY_KV, enrollment: needs ENROLLMENT_OPEN}.
const ROUTES = {
  '/join': { method: 'GET', handler: page, enrollment: true },
  '/join/issue': { method: 'POST', handler: joinIssue, kv: true, enrollment: true },
  '/enroll': { method: 'POST', handler: enroll, kv: true, enrollment: true },
  '/pro': { method: 'GET', handler: page, enrollment: true },
  '/pro/claim': { method: 'POST', handler: claim, kv: true, enrollment: true },
  '/relay': { method: 'POST', handler: relay, kv: true },
  '/ack': { method: 'POST', handler: ack, kv: true },
  '/public/': { method: 'GET', handler: publicRead, kv: true },
  '/admin/': { method: 'POST', handler: admin, kv: true },
};

function dispatch(request, env, path, route) {
  if (request.method !== route.method) return fail('method_not_allowed', 405);
  if (route.enrollment && env.ENROLLMENT_OPEN !== '1') {
    return route.method === 'GET' ? closedPage() : fail('enrollment_closed', 503);
  }
  if (route.kv && !env.RELAY_KV) return fail('relay_not_configured', 503);
  return route.handler(request, env, path);
}

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname.replace(/\/+$/, '') || '/';
    const route = ROUTES[path] || ROUTES[path.slice(0, path.indexOf('/', 1) + 1)];
    if (route) return dispatch(request, env, path, route);
    if (env.ASSETS) return secured(await env.ASSETS.fetch(request));
    return fail('not_found', 404);
  },
};
