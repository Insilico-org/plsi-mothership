# `mother` modules

`cli.py` maps each command to one function and prints its JSON envelope; every command is audited, a refusal as
`<command>_refused`.

| Area | Modules |
|---|---|
| Wire checks | `gate` (every message, WIRE §3), `contribution` (a contribution's coded fields), `note` (a research note's context, files and project), `flags` (warnings, never refusals), `filecheck` and `bundle` (data files) |
| Intake | `intake` (relayed messages: contributions, withdrawals, registry updates, the findings ledger), `mail` (parsing emails and saving digests), `research` (§7), `feedback` (§8), `fetch` (Drive files) |
| Decisions | `notes` (registry updates), `findings` and `ledger` (the findings ledger), `audit` (the access log and the steward check) |
| Results | `release`, `catalog`, `stats` (the §5 analysis ops), `publish`, `report`, `dashboard` (the stewards' Google Sheet) |
| Plumbing | `store` (local or Drive storage), `net` (the relay client), `professional` (invites and revocation), `index`, `clock`, `vocab` (every enum), `errors` |

`net.py` is the only module that talks to the relay, and `store.py` the only one that talks to Google (the store, the
dashboard Sheet and the shared Drive files `fetch` reads). Shared helpers live with their concern:
`flags.external` wraps text handed on as `external_text`, `store.new_receipt` mints a receipt, `clock.since` and
`clock.day` give the stamp N days back and check a `--date`/`--since` argument.
