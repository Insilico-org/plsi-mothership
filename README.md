# Longevity Mothership

The Longevity Mothership is a longevity research initiative run by Insilico Medicine. People who use
[PLSI](https://github.com/Insilico-org/PLSI), a personal longevity assistant that runs on Grok, can choose to share
deidentified health data with it. The pooled data helps us learn how health changes with age and which actions
shift it. Joining is optional, and PLSI works the same without it.

This repository is the receiving end. It has two parts:

- **The relay**, a small web service on Cloudflare. PLSI sends files through it, and it hands them on.
- **The Mother**, a Grok Bot run by Insilico's data stewards. It checks what arrives, stores it, deletes it on
  request, and builds the pooled results.

PLSI itself, the part on your computer, lives in the PLSI repository.

## What you can share

You choose each kind separately when you join:

- **Your health states**: values PLSI computed from your records, such as blood markers, and your medications and
  conditions as standard medical codes. Each is dated to the month.
- **Your life story**: health events from your history, such as conditions, treatments and lifestyle periods, as
  standard medical codes. Each is dated to the year.
- **Your prediction scores**: how well PLSI's predictions matched your later results, as counts and averages only.

Each file describes you only by sex, a five-year age band and, if you gave one, a broad ethnicity group.

**What never leaves your computer:** your name, contact details, addresses, employer, record or ID numbers, exact
dates, your birth year or age, the documents you uploaded, and any words from you or your records. Only codes,
numbers and units are sent. The records of family members you added to PLSI are never sent either.

The full terms you agree to are in PLSI's [consent text](https://github.com/Insilico-org/PLSI/blob/main/mothership/consent.md).

## How your data travels

1. **Joining.** You open the relay's join page once and give PLSI the one-time code it shows. PLSI then makes a
   random contributor code on your computer. Your files carry that code instead of your identity.
2. **Sending.** PLSI sends at most one file a week over an encrypted connection. The relay holds it unchanged
   until the Mother collects it, at most a day. After that the relay keeps only the receipt, "stored" or
   "refused", for seven days so PLSI can confirm the file arrived.
3. **Checking.** The Mother checks every file again before storing it. A file with free text, an exact date or
   anything else that should not be there is refused whole, never edited.
4. **Storing.** Files are kept in a Google Drive shared drive at Insilico that only the data stewards can open.

Each party knows only what it needs:

| | Knows | Never learns |
|---|---|---|
| PLSI, on your computer | everything about you, and the secret behind your contributor code | anyone else |
| The relay | your internet address while you are connected (it keeps no logs), and which kind of file you sent | which contributor code is yours |
| The Mother | your contributor code and what you shared | your internet address, or how your PLSI connects to the relay |

## Stopping and deleting

You can stop sharing at any time ("stop sharing with the Mothership"). You can also ask the Mothership to delete
everything it holds under your contributor code. The request carries a secret that only your PLSI holds, so nobody
else can delete your data or pretend to be you. Deletion is automatic and needs no one's approval. The Mother
replies with a deletion receipt, and PLSI shows it to you.

If you join again later, you start fresh with a new contributor code. It cannot be linked to the old one.

## What the Mothership gives back

- **A public catalog**: which kinds of data the Mothership holds, and roughly how many people share each.
- **Data releases** for verified researchers: averages and spreads by age band and sex, never one person's values.
  A group with fewer than ten people is left out.
- **Findings**: results that researchers registered before looking at the data, then checked against a release.
  A steward approves each one before it is published.

Researchers and physicians are verified by a steward before they get access. Each receives a personal access link
that a steward can revoke.

## Research notes and feedback

Research you do with PLSI is sent differently: by email, from your own Grok email account, and only after you
approve it. The note is signed with your address, so the Mothership can reply, and so that whoever sends research
stands behind it. The Mother reviews each note, a steward decides on it, and you get a reply with the decision.
Data files attached to a note are checked the same way as shared data.

Feedback on PLSI also goes by email and needs no joining. It is stored without your address. The stewards see a
weekly summary: where setup takes time, what frustrates people, and what they suggest.

## For developers and stewards

The exact message formats, endpoints and storage layout are in [contracts/WIRE.md](contracts/WIRE.md), the one
contract PLSI, the relay and the Mother all follow. Changing it means updating all three at once.

```
contracts/WIRE.md   the contract between PLSI, the relay and the Mother
relay/              the relay (Cloudflare Pages Functions); see relay/README.md
mother.md           first-run setup for the Mother's Grok Bot
steward.md          setup for a steward's own computer
persona.md          the Mother's rules
skills/             how the Mother reviews, decides and replies
src/mother/         the `mother` command-line tool the Mother uses
```

The Mother's judgment (reviewing notes, verifying professionals, drafting findings) lives in its skills. Every
check, the deletions, the statistics and the audit log are code, and code can only make the Mother's decisions
stricter.

### Running the tests

```bash
cd relay && npm test         # the relay, offline
```

### Secrets

Secrets are never kept in the repository and never printed.

- **The Mother's Grok Bot** reads these from its secret store: `MOTHER_STORE`, `MOTHER_DRIVE_CREDENTIALS`,
  `MOTHER_RELAY_URL`, `MOTHER_RELAY_INTAKE_KEY`, `MOTHER_STEWARDS`, `MOTHER_ACTOR`.
- **Stewards' own computers** hold `MOTHER_RELAY_ADMIN_KEY`, which publishes results and invites or revokes
  professionals. The bot never holds it: the bot prepares, and a steward publishes ([steward.md](steward.md)).
- **The relay** (Cloudflare secrets): `TOKEN_HMAC_SECRET`, `MOTHERSHIP_WEBHOOK_URL`, `MOTHERSHIP_SENDER_KEY`,
  `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`, `ADMIN_KEY` and `INTAKE_KEY`. Variables: `ENROLLMENT_OPEN` (`"1"`
  opens joining) and the optional `PUBLIC_FINDINGS`. Storage is the KV binding `RELAY_KV`.

### Storage

Production storage is a shared drive in Insilico's Google Workspace (`MOTHER_STORE=drive:<id>`). Only the Mother's
service account and the stewards' own service accounts can reach it, and there is no link sharing. Each account
must be a **Manager** of the drive: a Content manager cannot delete for good, so deleted files would only go to the
trash. `MOTHER_STORE=local:<dir>` is for development and tests.

## License

See [LICENSE](LICENSE).
