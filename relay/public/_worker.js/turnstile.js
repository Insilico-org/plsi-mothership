// The human check on the two pages that hand something out: /join (an enroll code) and /pro (a professional token).

import { fail, html } from './http.js';

// The static page public/<name>.html with the Turnstile widget (action = name), or a misconfigured notice, filled in.
export async function page(request, env, name) {
  const asset = env.ASSETS ? await env.ASSETS.fetch(new Request(new URL(`/${name}`, request.url))) : null;
  if (!asset || !asset.ok) return fail(`${name}_not_configured`, 503);
  const sk = String(env.TURNSTILE_SITE_KEY || '').replace(/[^0-9A-Za-z_-]/g, '');
  const widget = sk
    ? `<div class="cf-turnstile" data-sitekey="${sk}" data-action="${name}" data-theme="auto"></div>
  <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async defer></script>`
    : '<p class="warn"><strong>Misconfigured:</strong> the relay has no Turnstile site key, so nothing can be issued.</p>';
  return html((await asset.text()).replace('<!--WIDGET-->', widget).replace('<!--DISABLED-->', sk ? '' : ' disabled'));
}

// What /join and /pro show while ENROLLMENT_OPEN is not "1".
export const closedPage = () => html(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Longevity Mothership: not open yet</title><link rel="stylesheet" href="/relay.css"></head>
<body><h1>Not open yet</h1><p>The Longevity Mothership is not taking new members yet. Please come back later.</p></body></html>
`, 503);

// Null when the body's turnstileToken passes siteverify for this page's action and this request's hostname, else the
// error Response (a token solved on another page or site does not count).
export async function human(request, env, body, action) {
  const token = body && typeof body.turnstileToken === 'string' ? body.turnstileToken : '';
  if (!token || token.length > 2048) return fail('turnstile_required', 403);
  const form = new URLSearchParams({ secret: env.TURNSTILE_SECRET_KEY, response: token });
  const ip = request.headers.get('CF-Connecting-IP');
  if (ip) form.set('remoteip', ip);
  const verdict = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', { method: 'POST', body: form })
    .then((r) => r.json()).catch(() => ({}));
  const ok = verdict && verdict.success === true && verdict.action === action
    && verdict.hostname === new URL(request.url).hostname;
  return ok ? null : fail('turnstile_failed', 403);
}
