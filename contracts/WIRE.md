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
| `plsi_contribution` | install | `contributor_id`, `consent_version`, `packed` ("YYYY-MM"), `kinds` [states, life_events, scores], `subject` {`sex`, `age_band`, `ethnicity`?}, optional sections `states`, `life_events`, `scores` (shape below) |
| `plsi_withdrawal` | install | `contributor_id`, `request` ("stop" \| "delete_all"), `proof` (= `s`, hex; **required** for delete_all, **absent** for stop) |
| `plsi_research_note` | email only (§7), never the relay | `items` [{`claim`, `grade`, `evidence_class`, `sources` [{`kind`, `id`}], `falsified_by`, `next_step`; optional `question`, `topic` ("target" \| "repurposing" \| "method" \| "hallmark"), `support`, `against`, `risks` [{`source` (one of the item's `sources`), `finding`}], `limits` [text], `model_output` [{`model`, `finding`}]; each text 1-300 characters, each list 1-10 entries}], `method`; optional `report`: the compiled report in markdown, at most 40,000 characters; optional `files` [{`name`, `sha256`, optional `drive_id`}]: 1-20 data files (§7, Files) |
| `plsi_registry_update` | install | `items` [{`kind` ("physician" \| "method"), `name`, `specialty`, `country`, `public_url`, `methods`, `credentials`, `source_urls`}] |
| `plsi_preregistration` | professional (`findings:submit`) | `professional_id`, `preregistration_id`, `hypothesis`, `release`, `analysis` (§5 spec), `outcome`, `alpha` (number), `direction` ("positive" \| "negative" \| "two_sided"), `parents` [finding ids] |
| `plsi_finding` | professional (`findings:submit`) | `professional_id`, `finding_id`, `preregistration_id`, `release`, `analysis` (§5 spec, identical to the preregistration's), `result` (§5), `summary` |
| `plsi_feedback` | email only (§8), never the relay | `role` ("individual" \| "physician" \| "researcher"), `stages` [{`stage`, `minutes`, `measured`}], `frustrations` [{`stage`, `what`, `severity`}], `suggestions` [{`stage`, `what`}] |

**Consent kinds** are `states`, `life_events`, `scores`: consent kind and package section are one word.

**Contribution shape: coded fields only, no free text leaves PLSI.** Every object holds these fields and no others
(`?` = optional); anything else is `unknown_field`.
- `subject` {`sex`? ∈ M, F, X, unknown; `age_band`? `<lo>-<lo+4>` with lo a multiple of 5 in 0–115, or `90+`;
  `ethnicity`? ∈ european, african, middle_eastern, south_asian, east_asian, southeast_asian, latin_american, other
  (absent when not given; never `unspecified`)}; any other value is `bad_format`;
  `consent_version` `\d+\.\d+\.\d+`.
- `states[]` {`map_id`, `map_version`? `[0-9A-Za-z.+-]{1,32}`, `map_hash`? 64 lowercase hex (no `sha256:` prefix),
  `as_of`? month, `gaps`? [ID], `coordinates` {ID: coordinate}}. A coordinate is {`status`? `present` | `stale` | `missing` | `error`, `value`? number,
  `unit`?, `comparator`? (`<`, `<=`, `>`, `>=`), `observed`? month, `series`? [{`t` month, `value` number, `n`? integer ≥ 1}: at most one
  point per month, that month's mean over `n` recorded values, else `repeat_month`; `value` is the mean of its own
  `observed` month too],
  `items`? [{`code`, `clinical_status`? `active` | `stopped` | `resolved` | `unknown`, `dose`?}]}. A set item without a code is not packed; no `text` or
  `display`.
- `life_events[]` {`kind` ID, `year` int, `until`? int (both 1900–2100), `basis` ID, `code`, `value`? number, `unit`?,
  `clinical_status`? `active` | `stopped` | `resolved` | `unknown`, `dose`?}. An event without a code is not packed; no `label`, no `value_text`; `note` is not a
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
    So is `items[].support|against|risks[].source.id`, which must name one of the item's `sources`.
- **A research note's `files`:** `name` is `[a-z0-9][a-z0-9_-]{0,79}` with `.csv`, `.tsv`, `.md` or `.json`, unique
  in the note and exempt from `url` only; `sha256` is 64 lowercase hex digits and `drive_id` a Drive file id
  (`[A-Za-z0-9_-]{20,80}`), both exempt from `exact_date` and `id_number`.
- **A research note's `report`** gets every string rule; a violation's path is `report[<line>]` (the 1-based line
  where the match starts; one violation per line and rule), but `too_long` and a wrong type stay at `report`. Only
  there, these forms are blanked before the `exact_date` and `id_number` checks:
  - citations: `PMID`, `PMIDs` or `pubmed` (any case), then every id of a list (`PMID 12345678, 23456789`, `PMIDs: 1,
    2 and 3`); `NCT\d{8}`; a DOI `10\.\d{4,9}/[A-Za-z0-9._;()/:-]+` (never `@ ? = + & %`);
  - ranges and intervals: a decimal number, `-` or `–` or ` to `, a decimal number (`0.71-0.95`, `0.71 to 0.95`),
    and a confidence level `\d{1,2}(\.\d+)?%\s*CI` (`95% CI`);
  - genome coordinates `chr<1-22|X|Y|M>:<digits>(-<digits>)?`; Ensembl `ENS[A-Z]*[GTPE]\d{11}`, UniProt
    `[OPQ][0-9][A-Z0-9]{3}[0-9]`, dbSNP `rs\d{3,10}`, Illumina CpG probes `c[gh]\d{8}`. A note's files get the same
    forms blanked in every cell and line.

  The `url` check blanks only `https://doi.org/<doi>` and `doi.org/<doi>`: any other URL is refused. The `email` and
  `internal_id` checks see the report as sent.
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
  `sex` is F or M, `age_band` a contribution age band and `ethnicity` a §5 group value, else `bad_spec`.

**Rule names** (the same on both gates; PLSI uses exactly these):

| Rule | When |
|---|---|
| `not_json`, `duplicate_key`, `too_deep` | strict JSON fails (path `$`) |
| `unknown_kind`, `schema_version`, `sender` | the kind is unknown (or a `kinds` entry is), the version is not the integer 4, the sender may not send the kind |
| `missing_field`, `unknown_field`, `forbidden_field` | a required field is absent (also `code`, `map_id`, `coordinates`, `result`); a field outside the kind or object (also in `subject` and in a `scores` cell); a forbidden key |
| `bad_section` | a section, `subject`, a coordinate or a list has the wrong container type |
| `repeat_month` | a coordinate's `series` holds more than one point for the same month |
| `bad_id`, `bad_code`, `bad_format`, `bad_number` | an ID, a code, a format (month, unit, age band, sex, ethnicity, comparator, versions, `map_hash`; also a non-string unit) or a number (also a wrong-type or bool number, a year out of range) fails |
| `too_long`, `exact_date`, `email`, `url`, `id_number`, `internal_id` | the string rules above (in a research note's `report` at `report[<line>]`) |
| `not_in_kinds`, `bad_contributor_id` | contribution rules above |
| `bad_request`, `unexpected_proof`, `bad_proof` | withdrawal rules above |
| `bad_items`, `bad_source` | research note and registry `items` not a list of objects; a source not in its strict form |
| `bad_context` | a research-note context field (§3) of the wrong shape or length, or a finding whose `source` is not one of the item's `sources` |
| `bad_files` | a research note's `files` that is not 1-20 entries of `{name, sha256[, drive_id]}` in the forms of §3, or repeats a name |
| `formula` | a file cell that starts with `=`, `+`, `-` or `@` and is not a number (a spreadsheet formula) |
| `embedded_data` | any text holding a `data:` URI, or a run of 120 or more base64 characters that mixes lower case, upper case and digits (a DNA or protein sequence or a hex hash does not): a picture or file hidden in text |
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
            "sex": "F", "ethnicity": "unspecified", "n": 23, "mean": -1.2, "sd": 3.4, "p25": -3.1, "p50": -1.0, "p75": 0.9}]}
```

Each cell uses one value per active contributor: the latest numeric `value` of that coordinate in their latest
contribution (of several states of one map, the one with the latest `as_of`), so one package never counts twice.
A cell is keyed by (`map_id`, `coordinate`, `unit`, `age_band`, `sex`, `ethnicity`), where `ethnicity` is the
subject's, or `unspecified` when the subject has none; `cells` are sorted by that key.
Withdrawn (`stop`) contributors are left out. Cells with `n < min_cell` are left out, and only counted in
`suppressed_cells`. Floats are rounded to 4 significant digits. The catalog has no ethnicity breakdown.

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
- `{"op": "compare_groups", "map_id", "coordinate", "group_a": {"age_band"?, "sex"?, "ethnicity"?}, "group_b": {...}}`.
  - Each group pools its matching cells.
  - Welch's t-test on the two pooled groups.
  - `effect` = mean_a − mean_b; 95% CI from the Welch t quantile.
- `{"op": "age_trend", "map_id", "coordinate", "sex"?: "F"|"M", "ethnicity"?}`.
  - Weighted least squares of the cell means on the age-band midpoint, with weights `n / sd²`.
  - The matching cells are pooled first, per band, over sex and ethnicity when those are absent.
  - `effect` = slope per year; SE from WLS; t with (bands − 2) df; at least 3 bands, else error `too_few_cells`.

**Groups:** a group (and the `sex`, `ethnicity` of `age_trend`) may name only `age_band` (an age band), `sex` (F or M)
and `ethnicity` (a contribution value or `unspecified`); a cell matches when it equals every key the group names, and
a key it does not name is pooled over. The matching cells, in release order, are pooled: n = Σnᵢ, mean = Σnᵢmeanᵢ / n,
SD = √((Σ(nᵢ−1)sdᵢ² + Σnᵢ(meanᵢ−mean)²) / (n−1)). A spec that names no `ethnicity` on a release whose contributors
have none gives the same result as before ethnicity.

**Result:** `{"effect", "interval": [lo, hi], "n", "statistic", "df", "p"}`. These are floats rounded to 6 significant
digits, with p two-sided. Both sides use the same stdlib implementation of the Student t CDF (regularized incomplete beta,
continued fraction), so they agree to 1e-6 relative.

## 6. Mother storage (Google Drive shared drive; `local:` directory for dev and tests)

Every stored message is the gated document re-serialised canonically (`json.dumps(doc, sort_keys=True,
separators=(",", ":"), ensure_ascii=False, allow_nan=False)`), never the raw text; the sha256 check against the
relay_ref uses the raw bytes.

Deletion is final: the service account is a Manager of the shared drive. If Drive refuses a delete (403, a Content
manager), the item goes to the drive's trash instead (emptied by Drive after 30 days) and the result carries
`trashed: true`: in a `delete_all` reply and its ACK (`{ok: true, receipt, kind, status: "deleted", packages_deleted,
trashed: true}`), and in `research decide`. The audit log records `deletion_trashed` or `research_trashed`.

```
intake/<contributor_id>/<receipt>.json     gated contributions, never edited
notes/<receipt>.json                       registry updates (pending review)
notes/decisions.jsonl                      registry decisions: {at, receipt, decision, by (a steward), reason}
research/pending/<receipt>/note.json       a gated research note (§7) until a steward decides, never edited
research/pending/<receipt>/sender.json     {address, received_at, dmarc}: the address only for the reply, never in a release, catalog or digest
research/accepted/<receipt>/               an accepted note, moved from pending/ with its sender.json
research/rejected/<receipt>/               a rejected note, moved from pending/; once replied, sender.json keeps only
                                           {received_at, dmarc}
research/log.jsonl                         append-only: {at, type: review|decision|replied, receipt, ...}; a decision
                                           carries reply: pending until a replied entry follows
research/digests/<YYYY-MM-DD>.md           the daily research digest, as sent to the stewards
feedback/reports/<receipt>/report.json     a gated feedback report (§8), never edited
feedback/reports/<receipt>/meta.json       {received_at, dmarc, sender_sha256}: no address
feedback/digests/feedback_<YYYYMMDD>.md    the weekly feedback digest, as sent to the stewards
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
  at most 80 characters>`. Body: the note in plain words for a human reader. The `plsi_research_note` body (§3),
  gate-checked by PLSI, is one `.json` attachment; an older PLSI puts it instead in one fenced block of the body,
  opened by a line ```` ```plsi ```` and closed by a line ```` ``` ````. Nothing else in the email is read as data. An
  image, video or audio part (a signature logo, a photo) is never read and never refuses the email: intake counts
  them as `images_ignored`. Pictorial data therefore never reaches the store, because only the note and its listed
  files are read.
- **Files:** a note's `files` without a `drive_id` travel in the email as one `.zip` attachment holding exactly them,
  flat, unencrypted, at most 10 MB packed, 9 MB per file and 20 MB in all (else `BAD_BUNDLE`; none when the note lists
  some: `NO_BUNDLE`). Each must match its `sha256` (`HASH_MISMATCH`) and pass the file checks of Shared files below,
  where any violation, a formula included, refuses the whole note as `GATE_FAILED` at `files[<n>]`; a note is never
  edited, so a formula is not neutralised. They are stored with the note (`files/<name>`), moved with it when
  accepted and dropped when it is rejected. A file with a `drive_id` stays in the sender's Drive, shared as Viewer
  with the Mother's service account (`email` of `longevity_mothership_drive` in `contact.yaml`): `mother research
  fetch --receipt <r> --name <file>` reads its Drive sha256 first, downloads only when it equals the note's, checks the
  downloaded bytes against the note's sha256 again (the file may change between the two reads), then
  returns it as `external_text` when it passes the file checks. A Drive file is stored only when a steward accepts the
  note with a copy (`--keep`: the checked copy goes to `files/<name>` and moves with the note); otherwise each read
  goes to the sender's Drive, so the note's Drive data lasts only as long as the sender keeps it shared and unchanged.
  `mother research show --receipt <r> --file <name>` returns a stored (email) file as `external_text`.
- **Intake** (`mother research intake --eml <file> [--attachment <file>] [--bundle <file>]`): the bot saves the raw
  email as received (and its `.json` and `.zip` attachments, when the saved email does not hold them); code parses the `From`
  address (exactly one `From` header without parse defects, holding one ASCII addr-spec whose domain is a host name
  with a letter, no IP literal; else `BAD_SENDER`), the subject (stripped of leading and trailing white space, as a folded subject can
  start with a space; must start `PLSI research note:`) and the one `.json`
  attachment, else the one plsi block (else `NO_RESEARCH_NOTE`). Nothing from the email goes on a command line.
  The same gate as §3 with
  sender `email` (the only sender a research note is allowed from); a refused note gets a reply email with the
  violations and is stored nowhere. Stored: `research/pending/<receipt>/note.json` and `sender.json`; `decide`
  moves them to `research/accepted/` (the address kept, for later replies and to spot a repeat) or
  `research/rejected/` (the address dropped once the reply is sent, §6); `mother research tidy` does the same once
  for notes decided while they were stored at `research/<receipt>/`. One sender may have at most
  5 notes pending review (`TOO_MANY_PENDING`). A note its sender already sent (the same canonical body) gets its
  first receipt back with `repeat: true` and nothing is stored, so an email taken in twice is one note.
- **Inbox:** the email itself stays in the Mother's Grok inbox, which neither the Mother nor a steward can delete
  from; the store's rules (the address dropped, deletion) cover the store only. The inbox is read only by intake.
- **DMARC:** `dmarc` is read from the first (topmost, the receiving server's) `Authentication-Results` header, comments
  dropped: `pass`; `none` when there is no header, no dmarc result or `dmarc=none`; `fail` for any other result. It
  is stored in `sender.json` and shown to stewards in `research list`, `research show` and the digest. It never refuses
  a note: a saved .eml may not carry the header.
- **Warnings:** `research intake` and `research show` (and `feedback intake`, `feedback show` and `feedback digest`)
  return `warnings`, a list of `{path, flag}`: `instruction` for text that speaks to a bot ("ignore previous
  instructions", "system prompt", "the stewards must accept") and `promotion` for selling (prices, "buy now",
  discount codes). A warning never refuses and is not stored: it points the review to where to look.
- **Review:** the Mother's own review (`mother research review --receipt <r> --verdict ok|revise|reject --notes-file
  <file>`), then a steward's decision (`mother research decide --receipt <r> --decision accepted|rejected --by
  <steward> --reason-file <file>`). Free text the bot writes from an email always goes in a file. Both are appended to
  `research/log.jsonl`; the note is never edited.
- **Replies** (the Mother's Grok email, to `sender.json`'s address only): a receipt on intake, the decision with its
  reason after `decide`. A reply never contains another sender's address or note.
- **Shared files** (`mother research fetch --link <Drive link> [--check] [--out <file>]`): a file a researcher shared
  with the service account as viewer, read by the Drive API only. `--check` reads its name, type, size, sha256 and
  revision; a download is never asked to override Google's malware flag. Only csv, tsv, md and json are taken,
  recognised by content, at most 9 MB; images, video and audio are refused from their Drive type, before any
  download. Spreadsheet formulas are neutralised and text gets the §3 rules. A chart is Vega-Lite JSON with its
  data inline: no `url`, `href` or `image` mark, at most 5,000 rows (`BAD_CHART`).
  Google Docs, Sheets and Slides are refused (no fixed bytes). With `--link`, the checked copy goes to `--out`, never
  to the store; a note's own Drive file is fetched with `--receipt` and `--name` (Files above).
- **Daily digest** (`mother research digest --date <YYYY-MM-DD>`): that day's received, reviewed and decided notes with
  their claims, grades and decisions, and no addresses; the Mother summarizes it for the stewards and saves the text
  with `--save <file>`.

## 8. Feedback by email

Users' reports on how PLSI works for them, for its developers. Like research (§7) they come by email, so a user
needs no join or token; unlike research nothing is reviewed, decided or published.

- **Address:** the Mother's Grok email with the `+feedback` postfix. PLSI reads it from the `email` of
  `longevity_mothership_feedback` in `mothership/references/contact.yaml`.
- **Email:** sent from the user's own Grok email after their approval of that report. Subject `PLSI feedback: <n>
  frustrations, <m> suggestions`; body: the report in plain words, then one ```` ```plsi ```` block holding the
  `plsi_feedback` body (§3), gate-checked by PLSI.
- **Body rules:** the §3 gate with sender `email`, and: `stage` is one of `install`, `team`, `choices`, `interview`,
  `specialists`, `connectors`, `instance`, `routines`, `mothership`, `welcome` (the setup stages of PLSI's
  `plsi.md`), `records`, `plans`, `research`, `other`; `minutes` a whole number 0-1440, `measured` true when PLSI timed
  the stage, false when estimated; `severity` `low`, `medium` or `high`; `what` 1-300 characters, about PLSI, never
  about the person; each list at most 20 entries, not all three empty.
- **Intake** (`mother feedback intake --eml <file>`): the From, subject (stripped, must start `PLSI feedback:`, else
  `NO_FEEDBACK`) and block are parsed as in §7. Stored: `feedback/reports/<receipt>/`, with the sha256 of the address
  and never the address. The email stays in the Mother's Grok inbox, as in §7. The same report from the same sender gets its first receipt with `repeat: true`; at most 5
  reports per sender in seven days (`TOO_MANY_REPORTS`). The reply (a thank-you with the receipt, or the refusal) goes
  to the From address only.
- **Digest** (`mother feedback digest --since <YYYY-MM-DD>`): reports since that day by role; per stage the number of
  measured reports, median and longest minutes; frustrations per stage ranked by severity (high 3, medium 2, low 1)
  with their receipts; suggestions per stage. No addresses. `--save <file>` stores the Mother's digest text.
