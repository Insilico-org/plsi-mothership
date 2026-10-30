// POST /admin/* (§6). INTAKE_KEY, held by the Mother's bot for `mother intake`, opens only /admin/body and /admin/ack;
// ADMIN_KEY, held by stewards only, opens invites, invite status, revocation and publishing, and not body/ack. The two
// keys must differ: if they are equal, every admin and intake route answers 503 admin_not_configured.

import { postAck, takeBody } from './ack.js';
import { same } from './sign.js';
import { bearer, fail, json, readJson } from './http.js';
import { invite, inviteStatus } from './invites.js';
import { PUBLISHED, VERSION } from './reads.js';
import { UUID } from './tokens.js';

const PUBLISH_MAX = 5 * 1024 * 1024;
const INTAKE_MAX = 8 * 1024; // {ref} or {ref, ack} with the ACK at most 4 KiB
const INTAKE = { '/admin/body': takeBody, '/admin/ack': postAck };

async function publish(env, body) {
  const { what, version } = body;
  if (!body.body || typeof body.body !== 'object') return fail('bad_body', 400);
  const value = JSON.stringify(body.body);
  if (what === 'release') {
    if (typeof version !== 'string' || !VERSION.test(version) || version === 'latest') return fail('bad_version', 400);
    await env.RELAY_KV.put(`pub:release:${version}`, value);
    await env.RELAY_KV.put('pub:release:latest', version);
  } else if (Object.hasOwn(PUBLISHED, what)) {
    await env.RELAY_KV.put(PUBLISHED[what], value);
  } else {
    return fail('bad_what', 400);
  }
  return json({ ok: true });
}

export async function admin(request, env, path) {
  const intake = Object.hasOwn(INTAKE, path) ? INTAKE[path] : null;
  const key = intake ? env.INTAKE_KEY : env.ADMIN_KEY;
  if (env.INTAKE_KEY && env.INTAKE_KEY === env.ADMIN_KEY) return fail('admin_not_configured', 503);
  if (!key || !env.TOKEN_HMAC_SECRET) return fail(intake ? 'intake_not_configured' : 'admin_not_configured', 503);
  if (!same(bearer(request), key)) return fail('unauthorized', 401);
  const read = await readJson(request, intake ? INTAKE_MAX : PUBLISH_MAX);
  if (read instanceof Response) return read;
  const body = read.body;

  if (intake) return intake(env, body);
  if (path === '/admin/invite') return invite(request, env, body);
  if (path === '/admin/invite/status') return inviteStatus(env, body);
  if (path === '/admin/revoke') {
    if (typeof body.id !== 'string' || !UUID.test(body.id)) return fail('bad_id', 400);
    await env.RELAY_KV.put(`revoked:${body.id}`, '1');
    return json({ ok: true });
  }
  if (path === '/admin/publish') return publish(env, body);
  return fail('not_found', 404);
}
