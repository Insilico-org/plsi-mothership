// JSON responses, HTML page headers, the bearer token and bounded JSON bodies.

export const HEADERS = { 'content-type': 'application/json', 'cache-control': 'no-store' };
export const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: HEADERS });
export const fail = (error, status) => json({ ok: false, error }, status);
// 429 `rate_limited` with `retry_after` (whole seconds) in the body and a Retry-After header; 60 when the window end is
// unknown (a failed KV write).
export function limited(seconds = 60) {
  const res = json({ ok: false, error: 'rate_limited', retry_after: seconds }, 429);
  res.headers.set('retry-after', String(seconds));
  return res;
}
export const FORM_MAX = 4 * 1024; // the /join/issue, /enroll, /pro/claim and /ack bodies (§8)

// Every HTML page (and static asset) carries these (§8): only our own scripts plus Turnstile, no framing, no referrer.
export const SECURITY = {
  'content-security-policy': "default-src 'self'; script-src 'self' 'unsafe-inline' https://challenges.cloudflare.com; "
    + "frame-src https://challenges.cloudflare.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
  'x-content-type-options': 'nosniff',
  'referrer-policy': 'no-referrer',
};
export const html = (text, status = 200) => new Response(text, { status, headers: {
  'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store', ...SECURITY } });

// A copy of an asset response with SECURITY added.
export function secured(response) {
  const out = new Response(response.body, response);
  for (const [k, v] of Object.entries(SECURITY)) out.headers.set(k, v);
  return out;
}

export function bearer(request) {
  const m = (request.headers.get('authorization') || '').match(/^Bearer\s+(\S+)$/i);
  return m ? m[1] : '';
}

// Reads a JSON object body of at most `max` bytes: {raw, body} or an error Response. A declared content-length over
// the cap is refused unread, and the stream is cut off as soon as it passes the cap, so nothing big is ever buffered.
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
  const raw = new Uint8Array(size);
  let at = 0;
  for (const c of chunks) { raw.set(c, at); at += c.byteLength; }
  let body;
  try { body = JSON.parse(new TextDecoder().decode(raw)); } catch { return fail('bad_json', 400); }
  if (!body || typeof body !== 'object' || Array.isArray(body)) return fail('bad_json', 400);
  return { raw, body };
}
