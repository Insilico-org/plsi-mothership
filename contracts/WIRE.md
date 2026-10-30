# PLSI ↔ Longevity Mothership wire, v4

The single contract between PLSI (client, `Insilico-org/PLSI`), the relay (`relay/`) and the Mother toolbelt (`src/mother/`).
All three implement exactly this. A change here is a version bump on all three sides at once, with no compatibility path
for an earlier version. This version: contributions carry codes only, never free text (§3); a violation carries no sender text;
the Mother stores the gated document, not the raw text; the bot holds only the relay's intake key (§4); enrollment
opens by a switch.

## 1. Parties and what each knows

| Party | Knows | Never knows |
|---|---|---|
| PLSI (user's computer) | everything about its own user; the contributor secret `s` | other users |
| Relay (Cloudflare Pages Functions) | IP (at the edge), install token, professional token, the `kind` of each body | `install_id` → contributor link (it never forwards `install_id`), stored IPs |
| Mother (Grok Bot + `mother` CLI + Google Drive shared drive) | contributor pseudonym, package content, professional identity, the sender address of a research email | IP, install token, `install_id` |

Bodies are JSON, not yet sealed. Sealing to a Mother public key is planned. The relay parses a body only to read
`kind` (and `professional_id` for professional messages).

## 2. Identifiers and tokens

- **Install token** (relay-minted at `/enroll`, unchanged from v1): `plsi1.<b64url(install_id)>.<b64url(HMAC-SHA256(TOKEN_HMAC_SECRET, "v1:" + install_id))>`.
  `install_id` is a UUID. PLSI stores it in `<instance>/mothership/install.json` and never puts it in a body.
- **Contributor secret** `s`: 32 random bytes, hex (64 chars). PLSI generates it at the first `join` and stores it in
  `install.json` as `contributor_secret` (masked with `keys.hide`, never printed). It is kept across re-enrollment with a
  new code. It is dropped only after Mother's `delete_all` reply has `status: deleted` (keep `s`, or a sealed copy of
  the proof, until that ACK). A later join then makes a new `s`. Dropping `s` when `leave --delete` is sent, before
  that reply, means an empty or failed delete cannot be retried: the new `s` cannot prove `delete_all` for the old
  `contributor_id`.
- **Contributor id** (pseudonym): `contributor_id = sha256_hex(s)`, 64 lowercase hex. It is the only contributor
  identifier in any body.
- **Professional token** (relay-minted at `/pro/claim` from a steward's invite, after manual steward review):
  `plsp1.<b64url(payload_json)>.<b64url(HMAC-SHA256(TOKEN_HMAC_SECRET, "v1p:" + b64url(payload_json)))>` where
  `payload_json = {"pid": "<uuid>", "role": "physician"|"researcher", "scopes": [...], "exp": <unix seconds>}`
  (keys in this order, no spaces: `JSON.stringify` of that object). PLSI reads it from the env var
  `MOTHERSHIP_PROFESSIONAL_TOKEN` (via `plsi.keys`, set with Grok's secret card). PLSI decodes the payload locally for
  `whoami`; only the relay verifies the signature.
- **Scopes:** `releases:read`, `findings:read`, `findings:submit`.
- **Revocation:** KV key `revoked:<install_id or pid>` (no TTL). The relay refuses a revoked or expired token with
  401 `token_revoked` / `token_expired`.
- **Client-made ids:** preregistration `pr_<16 hex>`, finding `f_<16 hex>` (random). The Mother rejects a duplicate id.
  Receipts are Mother-made: `r_<16 hex>`.

## 3. Messages (all have `"schema_version": 4`, the integer: not `4.0`, not `true`)

| `kind` | Sender | Body fields (beyond `kind`, `schema_version`) |
|---|---|---|
| `plsi_contribution` | install | `contributor_id`, `consent_version`, `packed` ("YYYY-MM"), `kinds` [states, life_events, scores], `subject` {`sex`, `age_band`}, optional sections `states`, `life_events`, `scores` (shape below) |
| `plsi_withdrawal` | install | `contributor_id`, `request` ("stop" \| "delete_all"), `proof` (= `s`, hex; **required** for delete_all, **absent** for stop) |
| `plsi_research_note` | email only (§7), never the relay | `items` [{`claim`, `grade`, `evidence_class`, `sources` [{`kind`, `id`}], `falsified_by`, `next_step`}], `method` |
| `plsi_registry_update` | install | `items` [{`kind` ("physician" \| "method"), `name`, `specialty`, `country`, `public_url`, `methods`, `credentials`, `source_urls`}] |
| `plsi_preregistration` | professional (`findings:submit`) | `professional_id`, `preregistration_id`, `hypothesis`, `release`, `analysis` (§5 spec), `outcome`, `alpha` (number), `direction` ("positive" \| "negative" \| "two_sided"), `parents` [finding ids] |
| `plsi_finding` | professional (`findings:submit`) | `professional_id`, `finding_id`, `preregistration_id`, `release`, `analysis` (§5 spec, identical to the preregistration's), `result` (§5), `summary` |

**Consent kinds** are `states`, `life_events`, `scores`: consent kind and package section are one word.

**Contribution shape: coded fields only, no free text leaves PLSI.** Every object holds these fields and no others
(`?` = optional); anything else is `unknown_field`.
- `subject` {`sex`? ∈ M, F, X, unknown; `age_band`? `<lo>-<lo+4>` with lo a multiple of 5 in 0–115, or `90+`};
  `consent_version` `\d+\.\d+\.\d+`.
- `states[]` {`map_id`, `map_version`? `[0-9A-Za-z.+-]{1,32}`, `map_hash`? 64 lowercase hex (no `sha256:` prefix),
  `as_of`? month, `gaps`? [ID], `coordinates` {ID: coordinate}}. A coordinate is {`status`? ID, `value`? number,
  `unit`?, `comparator`? (`<`, `<=`, `>`, `>=`), `observed`? month, `series`? [{`t` month, `value` number}],
  `items`? [{`code`, `clinical_status`? ID, `dose`?}]}. A set item without a code is not packed; no `text` or
  `display`.
- `life_events[]` {`kind` ID, `year` int, `until`? int (both 1900–2100), `basis` ID, `code`, `value`? number, `unit`?,
  `clinical_status`? ID, `dose`?}. An event without a code is not packed; no `label`, no `value_text`; `note` is not a
  kind.
- `dose` is exactly {`value` number > 0, `unit`} (else `missing_field`, `unknown_field`, `bad_number`, `bad_format`;
  a non-object is `bad_section`). Doses are stored only: releases and the catalog do not use them.
- `scores` {`overall`?, `by_coordinate`? {ID: summary}}; a summary holds only `n_scored`, `n_missed`,
  `within_interval_rate`, `nominal_level`, `mean_abs_error`, `beat_persistence_rate`, `direction_accuracy`, each a
  number or null. `{}` when there are too few scores.
- `code` is exactly {`system`, `code`}: loinc `\d{1,7}-\d` with a valid LOINC mod-10 check digit, rxnorm
  `\d{1,10}`, snomed `\d{6,18}` with a valid Verhoeff check digit, icd10cm `[A-TV-Z]\d[0-9A-Z](\.[0-9A-Z]{1,4})?`,
  cpg and free `[a-z][a-z0-9_.:-]{0,63}`; else `bad_code`.
- ID = `[a-z0-9][a-z0-9_.-]{0,63}` (`map_id`, coordinate keys, `by_coordinate` keys, `gaps[]`, `kind`, `basis`,
  `clinical_status`, `status`); else `bad_id`. Month = `YYYY-MM` (`packed`, `as_of`, `observed`, `t`). `unit` =
  `[A-Za-z0-9%/.*^()\[\]{}'_-]{1,32}`, no whitespace. Formats above else `bad_format`. Numbers finite with
  |x| ≤ 1e9 and never a bool, else `bad_number`. A wrong container (an object where a list belongs, or the reverse) is
  `bad_section`.

**Gate rules (both PLSI's gate before sending and `mother intake` on arrival):**
- **Strict JSON:** NaN/Infinity are `not_json`, a duplicate key is `duplicate_key`, nesting deeper than 32 is
  `too_deep`, each at path `$` and checked before anything else.
- Known `kind` and the current `schema_version`, from its sender (`sender`); the fields of its kind only.
- **Forbidden keys anywhere:** `subject_id`, `instance`, `birth_year`, `event_id`, `event_ids`, `path`, `original_path`, `install_id`.
- **Strings:** every string and key is NFKC-normalised and stripped of format characters (Unicode `Cf`, e.g.
  zero-width space) before matching. A string is at most 4000 characters, a key at most 64 (`too_long`). No string
  (value or key; keys are never exempt) may contain:
  - `exact_date`: `YYYY-MM-DD`, `\b\d{4}[./-]\d{1,2}[./-]\d{1,2}\b`, `\d{1,2}[./-]\d{1,2}[./-]\d{2,4}` (one
    separator twice), a day with a month name and an optional `st|nd|rd|th` (`3 March 1980`, `3rd March 1980`,
    `Mar. 3, 1980`, `March 3rd, 1980`), or `\d{1,2}[- ]<month>[a-z]*\.?[- ]\d{2,4}` (`12-Mar-85`); case-insensitive;
  - `email` (a fullwidth `＠` too, by NFKC);
  - `url`: `http(s)://`, `www.`, or a domain with a common TLD (`com org net io ai co me info biz ru cn uk de xyz
    app dev link ly gl`, e.g. `evil.com/x`);
  - `id_number`: `\d[\d\s().-]{6,}\d` after removing years;
  - `internal_id`: `s_<12hex>`, `evt_<8-32hex>`.
- **Exemptions, by exact path only:**
  - id number: contribution `contributor_id`, `states[].map_hash`, and `code.code` only inside a code object of
    loinc, rxnorm, snomed or icd10cm (`states[].coordinates.<c>.items[].code.code`, `life_events[].code.code`); a
    free or cpg code has no exemption; withdrawal `contributor_id`, `proof`;
    preregistration `professional_id`, `preregistration_id`, `release`, `parents[]`; finding `professional_id`,
    `finding_id`, `preregistration_id`, `release`; research note `items[].sources[].id`;
  - URL, only in `plsi_registry_update`: `items[].public_url`, `items[].source_urls[]`;
  - research-note sources: each is exactly `{kind, id}` with `kind` pmid (`[1-9]\d{0,7}`), doi
    (`10\.\d{4,9}/\S{1,200}`), nct (`NCT\d{8}`) or model (letters required, `[A-Za-z0-9._:/-]{1,80}`), else
    `bad_source`; a source id in these strict public forms is also exempt from `exact_date` (preprint DOIs hold
    `2023.12.02`).
- **Contributions:** `kinds` ⊆ consented kinds (PLSI side); every present section's kind is in `kinds`
  (`not_in_kinds`); `contributor_id` is 64 hex (`bad_contributor_id`).
- **Withdrawals (Mother side):** `delete_all` requires `sha256_hex(proof) == contributor_id`. `proof` is the join-time
  contributor secret `s`. A withdrawal is accepted only from an install token (sender `contributor`). `stop` needs no
  proof, so any install token that knows a `contributor_id` could send it for that pseudonym; `contributor_id` is
  never published or shown (not in releases, the catalog, ACKs or digests), and that is the protection.
- **Violations carry no sender text.** A violation is `{path, rule}`. Paths are built from wire field names and
  indexes: a coordinate key is shown as `#key[<n>]`, any other key that is not a field name as `#unknown[<n>]`
  (n = its position), never the key text. No error message echoes an input value. A value of the wrong type
  (a number, list or object where a string belongs, or the reverse) never crashes a gate: it gets the field's rule.
- **Leave / delete order** (same bodies; not a new kind). PLSI waits for Mother's contribution ACK at `/ack` (the
  ticket from `/relay`; `ok: true` and a receipt, the contributor is then `active`) before `leave --delete`, and keeps
  the contributor secret until the withdrawal's ACK has `status: deleted`. Mother runs intake on each
  wake when it arrives and does not hold a withdrawal for a contribution that has not arrived. `delete_all` removes only
  packages already under `intake/<contributor_id>/`, and writes the deletion receipt first; a withdrawal that runs
  first can return `packages_deleted: 0` with `status: deleted`. That count is not proof an in-flight contribution was
  removed. A later `plsi_contribution` for a pseudonym that already has a deletion receipt (`sha256(contributor_id)`
  equals `contributor_id_sha256`) is refused `ALREADY_DELETED` and stored nowhere. `stop` does not do this; a later
  contribution under the same pseudonym is a re-join. A package still on disk for a deleted pseudonym is an orphan a
  steward removes (`intake/<contributor_id>/` and any index entry). That finishes the proven delete. A re-join cannot.
- **Professional messages:** `professional_id` is a UUID (`bad_id`) and equals the token's `pid`
  (`professional_mismatch`): the relay checks this, and the Mother checks it against the `professional_id` of the
  parked record it takes from `/admin/body`. `release` matches `[0-9A-Za-z._-]{1,64}` (`bad_release`). Analysis group
  `sex` is F or M and `age_band` a contribution age band, else `bad_spec`.

**Rule names** (the same on both gates; PLSI uses exactly these):

| Rule | When |
|---|---|
| `not_json`, `duplicate_key`, `too_deep` | strict JSON fails (path `$`) |
| `unknown_kind`, `schema_version`, `sender` | the kind is unknown (or a `kinds` entry is), the version is not the integer 4, the sender may not send the kind |
| `missing_field`, `unknown_field`, `forbidden_field` | a required field is absent (also `code`, `map_id`, `coordinates`, `result`); a field outside the kind or object (also in `subject` and in a `scores` cell); a forbidden key |
| `bad_section` | a section, `subject`, a coordinate or a list has the wrong container type |
| `bad_id`, `bad_code`, `bad_format`, `bad_number` | an ID, a code, a format (month, unit, age band, sex, comparator, versions, `map_hash`; also a non-string unit) or a number (also a wrong-type or bool number, a year out of range) fails |
| `too_long`, `exact_date`, `email`, `url`, `id_number`, `internal_id` | the string rules above |
| `not_in_kinds`, `bad_contributor_id` | contribution rules above |
| `bad_request`, `unexpected_proof`, `bad_proof` | withdrawal rules above |
| `bad_items`, `bad_source` | research note and registry `items` not a list of objects; a source not in its strict form |
| `professional_mismatch`, `bad_release`, `bad_spec`, `bad_alpha`, `bad_direction`, `bad_parents`, `bad_result` | professional rules above and §5 |

## 4. Relay HTTP API

| Method, path | Auth | Behaviour |
|---|---|---|
| GET `/join` * | none | join page; Turnstile **always** required (no env switch). Missing site key → page says misconfigured |
| POST `/join/issue` * | Turnstile (action `join`) | one-time enroll code `e1.<nonce>.<exp>.<sig>`, 900 s |
| POST `/enroll` * | code | `{ok, install_id, token}` (unchanged) |
| POST `/relay` | Bearer install or professional token | parse JSON (≤ 256 KiB). Kind must be allowed for the token type and scope (§3), else 403 `kind_not_allowed` (always for `plsi_research_note`). Park `{body, sender: contributor\|professional, professional_id?, role?}` (the body as sent; pid and role from a professional token) in KV as `body:<ref>` (random UUID, TTL 1 day), then POST `{kind: "relay_ref", schema_version, ref, sha256, bytes}` (of the body) to `MOTHERSHIP_WEBHOOK_URL` with `authorization: Bearer <MOTHERSHIP_SENDER_KEY>` and no other header: Grok Bot wakes show only ~4 KiB of a webhook body and the caller gets only Grok's run id. **Never** store or forward `install_id`. A 2xx webhook reply → 202 `{ok: true, ticket}` (`ticket` = `ref`); any other → 502 `upstream_error` with no upstream text, and the parked body is dropped |
| GET `/public/catalog` | none | KV `pub:catalog` (JSON) or 404 `not_published` |
| GET `/public/releases/latest`, `/public/releases/<version>` | professional `releases:read` | KV `pub:release:latest` (holds the version string) / `pub:release:<version>` |
| GET `/public/findings` | professional `findings:read`, or none when env `PUBLIC_FINDINGS` = "1" | KV `pub:findings` |
| GET `/pro` * | none | the professional claim page; Turnstile always required. It reads the invite code from the URL fragment (`/pro#<code>`), so opening the link sends nothing to the relay and spends nothing |
| POST `/pro/claim` * | invite code + Turnstile (action `pro`) | body `{invite, turnstileToken}` → `{ok, pid, role, scopes, exp, token}`, once per invite; then 400 `invite_already_used`. Unknown or past its 72 h → 400 `invalid_or_expired_invite`. 20 per hashed IP per hour |
| POST `/admin/invite` | Bearer `ADMIN_KEY` | body `{role, scopes (≥ 1), days (1-366)}` → `{ok, id, link, exp}`: `link` is `<relay>/pro#<code>` (24 random bytes), `exp` 72 h. KV holds `invite:<HMAC-SHA256(TOKEN_HMAC_SECRET, "inv:" + code)>` = `{role, scopes, days, exp}` (plus `pid`, `claimed_at` once claimed) for 90 days past `exp`; no email, no code. `id` is that HMAC |
| POST `/admin/invite/status` | Bearer `ADMIN_KEY` | body `{ids}` (≤ 100) → `{ok, invites: {<id>: {status: pending\|claimed\|expired\|unknown, pid?, claimed_at?}}}` |
| POST `/admin/revoke` | Bearer `ADMIN_KEY` | body `{id}` → KV `revoked:<id>` → `{ok}` |
| POST `/ack` | Bearer install or professional token | body `{ticket}` → `{ok, status: "pending"}` until the Mother posted, then `{ok, status: "done", ack}`; the ACK is `{ok: true, receipt, kind, status, ...counts}`, `{ok: false, error, violations: [{path, rule}]}`, or `{ok: false, error: "INTERNAL"}` on an unexpected Mother error |
| POST `/admin/ack` | Bearer `INTAKE_KEY` | body `{ref, ack}` (ack ≤ 4 KiB) → KV `ack:<ref>`, TTL 7 days → `{ok}`; `mother intake` posts it after every relay_ref it took (not on `REF_NOT_FOUND`, so a repeated wake keeps the first ACK) |
| POST `/admin/body` | Bearer `INTAKE_KEY` | body `{ref}` → `{ok, body, sender, professional_id?, role?}`, then deletes it; 404 `ref_not_found` when taken or expired. The Mother takes the sender only from here, never from webhook headers |
| POST `/admin/publish` | Bearer `ADMIN_KEY` | body `{what: "catalog"\|"release"\|"findings", version?, body}` (≤ 5 MiB) → KV; for release also sets `pub:release:latest` → `{ok}` |

\* Enrollment routes: open only while env `ENROLLMENT_OPEN` = "1"; otherwise the POSTs answer 503
`enrollment_closed` and the pages a plain "not open yet" page. Tokens already issued keep working on `/relay`, `/ack`
and `/public/*`.

**Keys:** `INTAKE_KEY` authorises only `/admin/body` and `/admin/ack`; it is the bot's key (`MOTHER_RELAY_INTAKE_KEY`).
`ADMIN_KEY` authorises invite, invite/status, revoke and publish, is not accepted on body/ack, and is never in the
bot's secret store: a steward runs those commands from their own terminal (`MOTHER_RELAY_ADMIN_KEY`). If the two
keys are equal, every admin and intake route answers 503 `admin_not_configured`.

**Other relay rules:**
- **Errors:** `{ok:false, error:<snake_case>}` with a fitting status (400/401/403/404/405/413/429/502/503).
- **Limits** (429 `{ok:false, error:"rate_limited", retry_after}` plus a `Retry-After` header, both the whole
  seconds until the limiting fixed window ends, at least 1; 60 when a failed KV write leaves the end unknown): install token 10 `/relay` per hour, professional token 30 per hour, `/ack` 60 per
  hour per token, and a global cap of 200 webhook forwards per hour (one KV counter, approximate: racing forwards can
  exceed it). Windows are fixed (`{count, start}`, expiring at the window's end), never a sliding TTL.
- **Bodies:** `/join/issue`, `/enroll`, `/pro/claim` and `/ack` read at most 4 KiB (413 `body_too_large`); every
  endpoint checks `content-length` first and stops reading past its cap.
- **Turnstile:** siteverify must return `success`, `action` equal to the page's action and `hostname` equal to the
  request's hostname.
- **HTML pages** carry `Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'
  https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; connect-src 'self';
  frame-ancestors 'none'; base-uri 'none'`, `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
- **Missing bindings:** `RELAY_KV` missing → 503 `relay_not_configured` for every endpoint that needs it. There is no cache fallback.
- **IP-based rate-limit keys** use `HMAC-SHA256(TOKEN_HMAC_SECRET, "ip:" + YYYY-MM-DD + ":" + ip)`, so no raw IP is
  ever stored and one IP is not linkable across days; an IPv6 `ip` is its /64 prefix. A per-IP window therefore also
  resets at UTC midnight.
- **No logging** of bodies, tokens or IPs.
- **Environment variables:**
  - `TOKEN_HMAC_SECRET`
  - `MOTHERSHIP_WEBHOOK_URL`
  - `MOTHERSHIP_SENDER_KEY` (only this one)
  - `TURNSTILE_SITE_KEY`
  - `TURNSTILE_SECRET_KEY`
  - `ADMIN_KEY`
  - `INTAKE_KEY` (a different random value)
  - `ENROLLMENT_OPEN` (`"1"` opens enrollment)
  - `PUBLIC_FINDINGS` (optional)
  - binding `RELAY_KV`

## 5. Data releases and analysis specs (identical maths on both sides)

**Release** (`mother release build`, published with `/admin/publish`):

```json
{"version": "2026-11", "built": "2026-11", "min_cell": 10, "suppressed_cells": 12,
 "cells": [{"map_id": "blood_phenoage", "coordinate": "phenoage_accel", "unit": "y", "age_band": "40-44",
            "sex": "F", "n": 23, "mean": -1.2, "sd": 3.4, "p25": -3.1, "p50": -1.0, "p75": 0.9}]}
```

Each cell uses one value per active contributor: the latest numeric `value` of that coordinate in their latest
contribution (of several states of one map, the one with the latest `as_of`), so one package never counts twice.
Withdrawn (`stop`) contributors are left out. Cells with `n < min_cell` are left out, and only counted in
`suppressed_cells`. Floats are rounded to 4 significant digits.

**Catalog** (`mother catalog`, public):

```json
{"built": "2026-11", "min_cell": 10, "contributors": "<10" | 37, "kinds": {"states": 30, "life_events": "<10", "scores": 12},
 "suppressed": 4,
 "coordinates": [{"map_id": "...", "coordinate": "...", "unit": "...", "contributors": 23, "months": ["2026-09", "2026-10"]}]}
```

Only active contributors count. A total below `min_cell` is the string `"<10"` (that is, `"<" + min_cell`). A
coordinate with fewer than `min_cell` contributors is not listed, nor is a month of a listed coordinate with fewer than
`min_cell` contributors; both are only counted in `suppressed`.

**Analysis spec**, one of:
- `{"op": "compare_groups", "map_id", "coordinate", "group_a": {"age_band"?, "sex"?}, "group_b": {...}}`.
  - Each group pools the matching cells (n-weighted mean, pooled SD).
  - Welch's t-test on the two pooled groups.
  - `effect` = mean_a − mean_b; 95% CI from the Welch t quantile.
- `{"op": "age_trend", "map_id", "coordinate", "sex"?: "F"|"M"}`.
  - Weighted least squares of the cell means on the age-band midpoint, with weights `n / sd²`.
  - Cells of different sex are pooled first, per band, when `sex` is absent.
  - `effect` = slope per year; SE from WLS; t with (bands − 2) df; at least 3 bands, else error `too_few_cells`.

**Result:** `{"effect", "interval": [lo, hi], "n", "statistic", "df", "p"}`. These are floats rounded to 6 significant
digits, with p two-sided. Both sides use the same stdlib implementation of the Student t CDF (regularized incomplete beta,
continued fraction), so they agree to 1e-6 relative.

## 6. Mother storage (Google Drive shared drive; `local:` directory for dev and tests)

Every stored message is the gated document re-serialised canonically (`json.dumps(doc, sort_keys=True,
separators=(",", ":"), ensure_ascii=False, allow_nan=False)`), never the raw text; the sha256 check against the
relay_ref uses the raw bytes.

```
intake/<contributor_id>/<receipt>.json     gated contributions, never edited
notes/<receipt>.json                       registry updates (pending review)
notes/decisions.jsonl                      registry decisions: {at, receipt, decision, by (a steward), reason}
research/<receipt>/note.json               a gated research note (§7), never edited
research/<receipt>/sender.json             {address, received_at, dmarc}: the address only for the reply, never in a release, catalog or digest
research/log.jsonl                         append-only: {at, type: review|decision, receipt, ...}
research/digests/<YYYY-MM-DD>.md           the daily research digest, as sent to the stewards
index.json                                 contributor_id -> {receipts, kinds, consent_version, status, last_packed}
deletions/<receipt>.json                   deletion receipts: {receipt, at, contributor_id_sha256 (hash of the pseudonym), packages_deleted}
releases/<version>/release.json            §5
catalog.json                               §5
findings/ledger.jsonl                      append-only: {at, type: preregistration|finding|check|review|decision, id, ...}
findings/<id>/                             message bodies, check outputs
audit/audit.jsonl                          every CLI read/write: {at, actor, action, target}
professional/invites.jsonl                 {id, role, scopes, days, invited, exp} per invite; no email, name, link or token
```

## 7. Research by email

Research notes are signed by their sender's email address instead of a pseudonym: the address is the accountability
that keeps irresponsible research off the Mothership. There is no anonymous research channel.

- **Address:** the Mother's Grok email with the `+research` postfix, e.g. `<mother>+research@<domain>`. PLSI reads it
  from the `email` of `longevity_mothership_research` in `mothership/references/contact.yaml`. Mail to any other address of the Mother is never
  research intake.
- **Email:** plain text, sent from the user's own Grok email after their approval. Subject `PLSI research note: <claim,
  at most 80 characters>`. Body: the note in plain words for a human reader, then exactly one fenced block opened by a
  line ```` ```plsi ```` and closed by a line ```` ``` ````, holding the `plsi_research_note` body (§3) as JSON, gate-checked by PLSI. Nothing else
  in the email is read as data; attachments are ignored.
- **Intake** (`mother research intake --eml <file>`): the bot saves the raw email as received; code parses the `From`
  address (exactly one `From` header without parse defects, holding one ASCII addr-spec whose domain is a host name
  with a letter, no IP literal; else `BAD_SENDER`), the subject (must start `PLSI research note:`) and the one plsi
  block (else `NO_RESEARCH_NOTE`). Nothing from the email goes on a command line. The same gate as §3 with
  sender `email` (the only sender a research note is allowed from); a refused note gets a reply email with the
  violations and is stored nowhere. Stored: `research/<receipt>/note.json` and `sender.json`. One sender may have at most
  5 notes pending review (`TOO_MANY_PENDING`).
- **DMARC:** `dmarc` is read from the first (topmost, the receiving server's) `Authentication-Results` header, comments
  dropped: `pass`; `none` when there is no header, no dmarc result or `dmarc=none`; `fail` for any other result. It
  is stored in `sender.json` and shown to stewards in `research list`, `research show` and the digest. It never refuses
  a note: a saved .eml may not carry the header.
- **Review:** the Mother's own review (`mother research review --receipt <r> --verdict ok|revise|reject --notes-file
  <file>`), then a steward's decision (`mother research decide --receipt <r> --decision accepted|rejected --by
  <steward> --reason-file <file>`). Free text the bot writes from an email always goes in a file. Both are appended to
  `research/log.jsonl`; the note is never edited.
- **Replies** (the Mother's Grok email, to `sender.json`'s address only): a receipt on intake, the decision with its
  reason after `decide`. A reply never contains another sender's address or note.
- **Daily digest** (`mother research digest --date <YYYY-MM-DD>`): that day's received, reviewed and decided notes with
  their claims, grades and decisions, and no addresses; the Mother summarizes it for the stewards and saves the text
  with `--save <file>`.
