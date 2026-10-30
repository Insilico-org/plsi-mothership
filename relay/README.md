# Longevity Mothership relay

The Cloudflare Pages Functions relay between PLSI and the Mother, implementing the wire contract
([../contracts/WIRE.md](../contracts/WIRE.md) §2-§4). Pages advanced mode runs the `public/_worker.js/` folder (entry
`index.js`, one module per job: `join`, `invites`, `relay`, `ack`, `reads`, `admin`, `tokens`, `turnstile`, `limits`, `http`,
`sign`), which must sit at the root of the build output directory (`pages_build_output_dir = "public"`). `public/` also
holds the static landing page, `join.html`, `pro.html` and their stylesheet `relay.css`; the worker serves the two
pages at `/join` and `/pro` with the Turnstile widget filled in.

## Endpoints

| Method, path | Auth | Does |
|---|---|---|
| GET `/join` * | none | `join.html` with the Turnstile widget (always required); says misconfigured without a site key |
| POST `/join/issue` * | Turnstile (action `join`) | one-time enroll code `e1.<nonce>.<exp>.<sig>`, valid 900 s |
| POST `/enroll` * | code | spends the code: `{ok, install_id, token}` |
| GET `/pro` * | none | `pro.html` with the Turnstile widget; the page reads the invite code from the URL fragment, so opening the link spends nothing |
| POST `/pro/claim` * | invite code + Turnstile (action `pro`) | spends the invite once: `{ok, pid, role, scopes, exp, token}`; the page shows the token once |
| POST `/relay` | install or professional token | checks the token (signature, `revoked:<id>`, expiry) and that the body's `kind` is allowed for it (§3), then parks it with its sender and forwards a `relay_ref`: 202 `{ok: true, ticket}` |
| GET `/public/catalog` | none | the published catalog |
| GET `/public/releases/latest`, `/public/releases/<version>` | professional `releases:read` | a published release |
| GET `/public/findings` | professional `findings:read` (none when `PUBLIC_FINDINGS=1`) | the published findings |
| POST `/admin/invite` | `ADMIN_KEY` | `{role, scopes, days}` -> `{ok, id, link, exp}`, after manual steward review; the link works once, for 72 h |
| POST `/admin/invite/status` | `ADMIN_KEY` | `{ids}` (at most 100) -> `{ok, invites: {id: {status, pid?, claimed_at?}}}` |
| POST `/admin/revoke` | `ADMIN_KEY` | `{id}` (install id or pid) -> `revoked:<id>` |
| POST `/ack` | install or professional token | `{ticket}` -> `{ok, status: pending}` or `{ok, status: done, ack}`: the Mother's outcome |
| POST `/admin/ack` | `INTAKE_KEY` | `{ref, ack}` -> `ack:<ref>` for 7 days (posted by `mother intake`) |
| POST `/admin/body` | `INTAKE_KEY` | `{ref}` -> `{ok, body, sender, professional_id?, role?}` and deletes it (one take) |
| POST `/admin/publish` | `ADMIN_KEY` | `{what: catalog\|release\|findings, version?, body}` (at most 5 MiB); a release also moves `pub:release:latest` |

\* Enrollment routes, open only while `ENROLLMENT_OPEN` is `"1"`. Otherwise the POSTs answer 503 `enrollment_closed`
and the pages a plain "not open yet" page (503); tokens already issued keep working on `/relay`, `/ack` and `/public/*`.

Two admin keys. `INTAKE_KEY` opens only `/admin/body` and `/admin/ack`; the Mother's bot holds it for `mother intake`.
`ADMIN_KEY` opens invite, invite/status, revoke and publish, is not accepted on body/ack, and stays with the stewards,
who run those commands from their own terminal. A missing key answers 503 `intake_not_configured` /
`admin_not_configured`. Use two different random values: equal keys close every admin and intake route (503
`admin_not_configured`).

The relay reads a body only for `kind` and, on `plsi_preregistration` and `plsi_finding`, `professional_id` (must be the
token's pid). No body is forwarded as is: Grok Bot shows a webhook wake only its first ~4 KiB and answers the caller
with its own run id, so the relay parks every body in KV with its sender (`body:<random uuid>` = `{body, sender:
contributor|professional, professional_id?, role?}`, 1 day TTL, deleted when the Mother takes it), forwards `{kind:
"relay_ref", schema_version, ref, sha256, bytes}` with only `authorization: Bearer <MOTHERSHIP_SENDER_KEY>` and, when
the webhook answers 2xx, replies 202 `{ok: true, ticket}` (`ticket` = `ref`). Nothing of the webhook's reply reaches
the sender: any other webhook status is 502 `upstream_error` and the parked body is dropped. The Mother trusts no
webhook header: the sender comes from the parked record. It never stores or forwards `install_id`. The Mother posts
its outcome under the ref (`ack:<ref>`, 7 days) and the sender reads it at `/ack`. Errors are `{ok: false, error}`.

## Configuration

Secrets (Pages project settings, never in the repo): `TOKEN_HMAC_SECRET`, `MOTHERSHIP_WEBHOOK_URL`,
`MOTHERSHIP_SENDER_KEY`, `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`, `ADMIN_KEY`, `INTAKE_KEY`. Variables:
`ENROLLMENT_OPEN` (`"1"` opens joining and professional claims; unset or any other value keeps them closed) and
optional `PUBLIC_FINDINGS` (`"1"` opens `/public/findings`). The Turnstile widget must list the relay's hostname:
siteverify's `hostname` must equal the request's, and its `action` the page's (`join` or `pro`). KV binding `RELAY_KV`
(see `wrangler.toml`); without it every endpoint that needs it answers 503 `relay_not_configured`; so does a missing
`TOKEN_HMAC_SECRET` on `/relay` and the token-gated `/public/*` reads (never 401, which would tell clients to re-join).
An unreachable Mother is 503 `upstream_unavailable`.

## Limits

| What | Limit |
|---|---|
| `/relay` per install token | 10 per hour |
| `/relay` per professional token | 30 per hour |
| `/ack` per token | 60 per hour |
| webhook forwards, all tokens together (each wakes the Grok bot) | 200 per hour, approximate |
| `/join/issue`, `/pro/claim` per IP | 20 per hour |
| `/enroll` per IP | 40 per hour |
| `/join/issue`, `/enroll`, `/pro/claim`, `/ack` body | 4 KiB |
| `/relay` body | 256 KiB |
| `/admin/body`, `/admin/ack` body | 8 KiB (the ACK itself at most 4 KiB) |
| other `/admin/*` bodies | 5 MiB |

Over a limit is 429 `rate_limited` with `retry_after` (whole seconds, at least 1, until the limiting window ends) in
the body and a matching `Retry-After` header; a failed KV write, whose window end is unknown, says 60. An oversize body is 413 `body_too_large`: refused from `content-length` before
reading, or cut off as soon as the stream passes the cap. Windows are fixed: `rl:<key>` = `{count, start}` expires at
`start` + 1 hour, and a hit never extends it. A request counts only once its cheap checks pass (Turnstile on join and
claim, the token and `kind` on relay), so a failed Turnstile or a refused kind costs no KV write. IPv4 clients count
per address, IPv6 clients per /64.

### Limits are best-effort

Rate limits (`rl:*`), the one-time enroll code (`used:<nonce>`) and the one-time invite (`invite:<id>`) are KV
read-then-put: not atomic, and KV is eventually consistent across locations (up to about 60 s). Quotas are soft, and
one code or invite can, in a narrow race, be spent twice (two tokens from one invite share its pid, so one revoke stops
both). A KV write that fails (about 1 write/s per key) counts as a burst and answers 429 `rate_limited`. The global
forward counter is a single key every forward writes, so racing forwards can lose counts and a failed write there is
ignored: 200/hour can be exceeded by however many forwards race at once. If one-time use or the caps must hold
strictly, move code spending and counters to a Durable Object or the Rate Limiting binding.

## Headers and logging

- Pages and static assets carry `Content-Security-Policy` (own scripts plus Turnstile only, no framing, `base-uri
  'none'`), `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`. The policy allows no inline styles,
  so the pages' CSS lives in `relay.css`.

The relay must not keep anything that joins a connection to a submission:
- Workers logs and observability stay **off** for the Pages project (no `console.*` in the code, no Logpush, no tail).
- No body, token or IP is logged. A parked body and an ACK are stored under a random id with nothing about the
  sender; the ticket alone reads the ACK. IP rate-limit keys are `HMAC-SHA256(TOKEN_HMAC_SECRET, "ip:" + date + ":" + ip)`
  (for IPv6, `ip` is the /64 prefix); token rate-limit keys are HMACs of the id too. The UTC date in the IP key stops
  linking one IP across days; it also means a per-IP window resets at UTC midnight.

## Test and deploy

```sh
npm test            # node --test, offline: fake KV and assets, stubbed Turnstile and Mother
npm run upload && RELAY_WORKER=upload npm test    # the same tests against the drag-and-drop bundle
npx wrangler pages dev public     # check before deploying: GET /relay answers 405, GET /join and GET /pro show their pages
npx wrangler pages deploy public --project-name=plsi-mothership-relay
```

For a dashboard drag-and-drop deploy (Direct Upload), which takes a single `_worker.js` file but not the module
folder: `npm run upload` bundles the worker into `upload/_worker.js` (esbuild) next to the HTML pages and `relay.css`;
drop the `upload/` folder.

Before deploying this version: add the secret `INTAKE_KEY` (a new random value, different from `ADMIN_KEY`) in the
Pages project settings and give the same value to the Mother's bot as `MOTHER_RELAY_INTAKE_KEY`; remove the admin key
from the bot's secret store and rotate `ADMIN_KEY`, since the bot held it. Set the variable `ENROLLMENT_OPEN` = `1`
only when joining should open.

The deploy is run by a human after review.
