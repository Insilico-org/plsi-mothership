// POST /admin/* (WIRE §4): INTAKE_KEY opens only the intake routes, ADMIN_KEY only the rest; equal keys close both.

import { postAck, takeBody } from './ack.js';
import { MAX } from './config.js';
import { bearer, fail, json, readJson } from './http.js';
import { invite, inviteStatus } from './invites.js';
import { publish } from './reads.js';
import { same } from './sign.js';
import { isUuid, revoke } from './tokens.js';

async function revokeId(env, body) {
  if (!isUuid(body.id)) return fail('bad_id', 400);
  await revoke(env, body.id);
  return json({ ok: true });
}

// path -> {handler(env, body, request), intake: opened by INTAKE_KEY and carries a {ref}}.
const ADMIN = {
  '/admin/body': { handler: takeBody, intake: true },
  '/admin/ack': { handler: postAck, intake: true },
  '/admin/invite': { handler: invite },
  '/admin/invite/status': { handler: inviteStatus },
  '/admin/revoke': { handler: revokeId },
  '/admin/publish': { handler: publish },
};

export async function admin(request, env, path) {
  const route = Object.hasOwn(ADMIN, path) ? ADMIN[path] : null;
  const intake = Boolean(route && route.intake);
  const key = intake ? env.INTAKE_KEY : env.ADMIN_KEY;
  if (env.INTAKE_KEY && env.INTAKE_KEY === env.ADMIN_KEY) return fail('admin_not_configured', 503);
  if (!key || !env.TOKEN_HMAC_SECRET) return fail(intake ? 'intake_not_configured' : 'admin_not_configured', 503);
  if (!same(bearer(request), key)) return fail('unauthorized', 401);
  const read = await readJson(request, intake ? MAX.intake : MAX.publish);
  if (read instanceof Response) return read;
  if (!route) return fail('not_found', 404);
  if (intake && !isUuid(read.body.ref)) return fail('bad_ref', 400);
  return route.handler(env, read.body, request);
}
