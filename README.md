# plsi-mothership

The Longevity Mothership side of [PLSI](https://github.com/Insilico-org/PLSI): the **relay** that PLSI installs and
verified professionals talk to, and the **Mother**, a Grok Bot with the `mother` toolbelt that stores, deletes,
aggregates and publishes. The PLSI repo holds only the client.

The single contract between PLSI, the relay and the Mother is [contracts/WIRE.md](contracts/WIRE.md). A change
there is a version bump on all three sides at once.

PLSI waits for Mother's contribution ACK before `leave --delete`, and keeps the join-time contributor secret until
Mother replies `deleted`. That secret is the `delete_all` proof (`sha256` equals `contributor_id`); a re-join cannot
delete the previous pseudonym. Mother refuses a contribution that arrives after a `delete_all` receipt for the same
pseudonym. A withdrawal that runs first can still report `packages_deleted: 0`; a package already stored for a deleted
pseudonym is an orphan a steward removes. Sequencing: [mother.md](mother.md) §6 and WIRE §3.

Research notes never take the relay: PLSI users send them from their own Grok email to the Mother's
`+research` address, signed by their address, which keeps irresponsible research off the Mothership. The Mother saves
each raw email and takes it in with `mother research intake --eml` (code parses the sender and the note and records
the email's DMARC result for the stewards; nothing from an email goes on a command line), reviews them, asks a steward to decide, replies to the sender with the receipt
and the decision, and sends the stewards a daily digest without addresses (WIRE §7, skill `research`). Notes are stored
under `research/`, apart from contributions and registry updates.

## Split knowledge (WIRE §1)

| Party | Knows | Never knows |
|---|---|---|
| PLSI (user's computer) | everything about its own user; the contributor secret `s` | other users |
| Relay (Cloudflare Pages Functions) | IP (at the edge), install token, professional token, the `kind` of each body | `install_id` → contributor link (it never forwards `install_id`), stored IPs |
| Mother (Grok Bot + `mother` CLI + Google Drive shared drive) | contributor pseudonym, package content, professional identity, a research sender's address | IP, install token, `install_id` |

## Layout

```
contracts/WIRE.md   the wire contract
relay/              Cloudflare Pages Functions: /join /enroll /pro /relay /public/* /admin/*
mother.md           first-run instruction for the Mother Grok Bot
steward.md          first-run instruction for a steward's own machine (admin key: publish, invites, revoke)
persona.md          the Mother's rules
skills/             the Mother's judgment: intake, deletion, catalog, registry, research, verify, findings, steward
src/mother/         the toolbelt (`mother` CLI, one JSON envelope per command): gate, store, intake, index,
                    release, catalog, stats, findings, ledger, notes, research, publish, professional, audit, report
```

Agents judge, code computes: the gate, cell sizes, deletion, statistics, the ledger and the audit log are code; the bot
decides reviews, notes and verifications, and its decisions are recorded by code that may only make them stricter.
Contributions carry codes only, never free text; the gate's violations are `{path, rule}` without the sender's text,
and the store keeps the gated document, canonically serialised (WIRE §3, §6).

## Tests

```bash
cd relay && npm test         # relay, offline: node --test with a faked KV and fetch
```

## Secrets

Never in the repo, never printed.

- **Mother bot** (Grok secret store, read from env): `MOTHER_STORE`, `MOTHER_DRIVE_CREDENTIALS` (service account
  file), `MOTHER_RELAY_URL`, `MOTHER_RELAY_INTAKE_KEY` (the relay's `INTAKE_KEY`: how `mother intake` takes each
  relayed message and posts its ACK), `MOTHER_STEWARDS`, `MOTHER_ACTOR`.
- **Stewards' machines only** ([steward.md](steward.md)): `MOTHER_RELAY_ADMIN_KEY` (the relay's `ADMIN_KEY`), read
  only by `mother publish` and `mother professional invite|invites|revoke`. It is never in the bot's secret store: the
  bot builds and shows, a steward publishes, invites and revokes. Invite links are written to a file the steward
  emails to the verified address (skill `verify`); the professional claims the token on the relay's `/pro` page.
- **Relay** (Cloudflare secrets): `TOKEN_HMAC_SECRET`, `MOTHERSHIP_WEBHOOK_URL`, `MOTHERSHIP_SENDER_KEY`,
  `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`, `ADMIN_KEY`, `INTAKE_KEY` (a different value); variables
  `ENROLLMENT_OPEN` (`"1"` opens `/join` and `/pro`) and optional `PUBLIC_FINDINGS`; KV binding `RELAY_KV`.

## Storage

Storage is a dedicated **shared drive in Insilico's Google Workspace** (`MOTHER_STORE=drive:<id>`),
reached only by the `mother` service account: no link sharing, steward accounts only, layout as in WIRE §6.
`local:<dir>` is for development and tests. Both sit behind `src/mother/store.py`, so a later move (e.g. to R2) is one
backend.
