// PLSI <-> Longevity Mothership relay (contracts/WIRE.md §2-§4). Pages advanced mode: the _worker.js/ folder, entry
// index.js. It never logs bodies, tokens or IPs, never stores a raw IP and never forwards install_id.

import { fail, secured } from './http.js';
import { ack } from './ack.js';
import { admin } from './admin.js';
import { claim } from './invites.js';
import { enroll, joinIssue } from './join.js';
import { publicRead } from './reads.js';
import { relay } from './relay.js';
import { closedPage, page } from './turnstile.js';

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname.replace(/\/+$/, '') || '/';
    // Enrollment routes answer only while ENROLLMENT_OPEN is "1"; tokens already issued keep working either way.
    const route = (method, needsKv, handler, enrollment = false) => {
      if (request.method !== method) return fail('method_not_allowed', 405);
      if (enrollment && env.ENROLLMENT_OPEN !== '1') return method === 'GET' ? closedPage() : fail('enrollment_closed', 503);
      if (needsKv && !env.RELAY_KV) return fail('relay_not_configured', 503);
      return handler();
    };

    if (path === '/join') return route('GET', false, () => page(request, env, 'join'), true);
    if (path === '/join/issue') return route('POST', true, () => joinIssue(request, env), true);
    if (path === '/enroll') return route('POST', true, () => enroll(request, env), true);
    if (path === '/pro') return route('GET', false, () => page(request, env, 'pro'), true);
    if (path === '/pro/claim') return route('POST', true, () => claim(request, env), true);
    if (path === '/relay') return route('POST', true, () => relay(request, env));
    if (path === '/ack') return route('POST', true, () => ack(request, env));
    if (path.startsWith('/public/')) return route('GET', true, () => publicRead(request, env, path));
    if (path.startsWith('/admin/')) return route('POST', true, () => admin(request, env, path));

    if (env.ASSETS) return secured(await env.ASSETS.fetch(request));
    return fail('not_found', 404);
  },
};
