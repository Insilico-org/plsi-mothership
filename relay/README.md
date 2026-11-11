# Longevity Mothership relay

The Cloudflare Pages relay between PLSI and the Mother. Its routes, bodies, error codes and keys are the wire contract,
[../contracts/WIRE.md](../contracts/WIRE.md) §4; this file holds only implementation notes.

Pages advanced mode runs the `public/_worker.js/` folder, which must sit at the root of the build output directory
(`pages_build_output_dir = "public"`). `public/` also holds the landing page, `join.html`, `pro.html` and their
stylesheet `relay.css`; the worker serves the two pages at `/join` and `/pro` with the Turnstile widget filled in.

| Module | Job |
|---|---|
| `index.js` | entry: the `ROUTES` table (method, KV need, enrollment gate) and dispatch; anything else goes to the static assets |
| `config.js` | every tunable: lifetimes (`TTL`), body caps (`MAX`), rate limits (`LIMITS`) |
| `join.js` | `/join/issue` and `/enroll`: one-time enroll codes for an install token |
| `invites.js` | `/admin/invite`, `/admin/invite/status` and `/pro/claim`: one-time professional invites |
| `relay.js` | `/relay`: kind gate, parking, the `relay_ref` forward |
| `ack.js` | `/ack`, `/admin/ack` and `/admin/body`: the Mother's ACK and the one-time body take |
| `reads.js` | `/public/*` and `/admin/publish` |
| `admin.js` | `/admin/*`: the key check and the `ADMIN` handler table |
| `tokens.js` | install and professional tokens: mint, verify, revoke |
| `turnstile.js` | the `/join` and `/pro` pages, the closed page, siteverify |
| `limits.js` | fixed-window rate limits and the shared form-route preamble |
| `http.js` | JSON and HTML responses, security headers, bounded body reads |
| `sign.js` | encoding, HMAC, hashing, constant-time comparison |

## Parking and forwarding

The relay reads a body only for `kind`, `professional_id` and a withdrawal's `request`. A Grok Bot webhook wake shows
only the first ~4 KiB of its body and answers the caller with its own run id, so no body is forwarded as is: the relay
parks it as `body:<random uuid>` with its sender and forwards only a `relay_ref`. A `plsi_withdrawal` with `request:
delete_all` also writes `deleting:<ref>` = the install id (as long as the ACK lives); once that install reads a
`deleted` ACK at `/ack`, its token is revoked. Both keys are written before the forward and dropped when it fails. A
failed KV write while parking is 503 `relay_unavailable`, an unreachable Mother 503 `upstream_unavailable`, any
non-2xx webhook reply 502 `upstream_error`; nothing of the webhook's reply reaches the sender.

## Configuration

Secrets (Pages project settings, never in the repo): `TOKEN_HMAC_SECRET`, `MOTHERSHIP_WEBHOOK_URL`,
`MOTHERSHIP_SENDER_KEY`, `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`, `ADMIN_KEY`, `INTAKE_KEY` (a different random
value). Variables `ENROLLMENT_OPEN` and optional `PUBLIC_FINDINGS`, KV binding `RELAY_KV` (see `wrangler.toml`), all
as in WIRE §4. The Turnstile widget must list the relay's hostname.

## Limits

Every number lives in `public/_worker.js/config.js`. Rate-limit windows are fixed hours (`rl:<key>`). A request counts
only once its cheap checks pass (Turnstile on join and claim, the token and `kind` on relay), so a failed Turnstile or
a refused kind costs no KV write. IPv4 clients count per address, IPv6 clients per /64. An oversize body is refused
from `content-length` before reading, or cut off as soon as the stream passes the cap.

Rate limits, the one-time enroll code (`used:<nonce>`) and the one-time invite (`invite:<id>`) are KV read-then-put:
not atomic, and KV is eventually consistent across locations (up to about 60 s). Quotas are soft, and one code or
invite can, in a narrow race, be spent twice (two tokens from one invite share its pid, so one revoke stops both). A
failed KV write (about 1 write/s per key) answers 429 `rate_limited`, except on the global forward counter, where it
is ignored. If one-time use or the caps must hold strictly, move code spending and counters to a Durable Object or
the Rate Limiting binding.

## Headers and logging

Pages and static assets carry the WIRE §4 security headers: the pages' own and inline scripts plus Turnstile, no
framing and no inline styles, so the pages' CSS lives in `relay.css`. Nothing may join a connection to a submission:
- Workers logs and observability stay **off** for the Pages project (no `console.*` in the code, no Logpush, no tail).
- No body, token or IP is logged. A parked body and an ACK are stored under a random id with nothing about the
  sender; the ticket alone reads the ACK. IP rate-limit keys are HMACs of the UTC date and the IP (or /64), token
  rate-limit keys HMACs of the id, so a per-IP window also resets at UTC midnight.

## Test and deploy

```sh
npm test            # node --test, offline: fake KV and assets, stubbed Turnstile and Mother
npm run upload && RELAY_WORKER=upload npm test    # the same tests against the drag-and-drop bundle
npx wrangler pages dev public     # check before deploying: GET /relay answers 405, GET /join and GET /pro show their pages
npx wrangler pages deploy public --project-name=plsi-mothership-relay
```

For a dashboard drag-and-drop deploy (Direct Upload), which takes a single `_worker.js` file but not the module
folder: `npm run upload` bundles the worker into `upload/_worker.js` (esbuild) next to the HTML pages and `relay.css`;
drop the `upload/` folder. The deploy is run by a human after review.
