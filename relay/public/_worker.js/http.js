// JSON and HTML responses, the bearer token and bounded JSON bodies.

import { dec } from './sign.js';

export const HEADERS = { 'content-type': 'application/json', 'cache-control': 'no-store' };
export const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: HEADERS });
export const fail = (error, status) => json({ ok: false, error }, status);

// 429 with retry_after and Retry-After in whole seconds; 60 when the window end is unknown (a failed KV write).
export function limited(seconds = 60) {
  const res = json({ ok: false, error: 'rate_limited', retry_after: seconds }, 429);
  res.headers.set('retry-after', String(seconds));
  return res;
}

const SECURITY = {
  'content-security-policy': "default-src 'self'; script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com; "
    + "frame-src https://challenges.cloudflare.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'no-referrer',
};
export const html = (text, status = 200) => new Response(text, { status, headers: {
  'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', ...SECURITY } });

export function secured(response) {
  const out = new Response(response.body, response);
  for (const [k, v] of Object.entries(SECURITY)) out.headers.set(k, v);
  return out;
}

export function bearer(request) {
  const m = (request.headers.get('authorization') || '').match(/^Bearer\s+(\S+)$/i);
  return m ? m[1] : '';
}

// A JSON object body of at most `max` bytes as {raw, text, body}, or an error Response; never buffers past the cap.
export async function readJson(request, max) {
  if (Number(request.headers.get('content-length')) > max) return fail('body_too_large', 413);
  const chunks = [];
  let size = 0;
  if (request.body) {
    const reader = request.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > max) {
        reader.cancel().catch(() => {});
        return fail('body_too_large', 413);
      }
      chunks.push(value);
    }
  }
  const raw = new Uint8Array(await new Blob(chunks).arrayBuffer());
  const text = dec.decode(raw);
  let body;
  try { body = JSON.parse(text); } catch { return fail('bad_json', 400); }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return fail('bad_json', 400);
  return { raw, text, body };
}
