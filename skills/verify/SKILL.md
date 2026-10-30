---
name: verify
description: Use when a physician or researcher asks to register with the Longevity Mothership, or a steward asks to renew or revoke a professional; the manual v0 review checklist, then a one-time invite, its status or a revocation, which a steward runs from their own terminal.
---

# Mother professional verification (v0, manual)

Follow persona.md. Registration v0 is by invitation: a steward reviews every request by hand. Professional identity is
known and verified; it is never stored with, compared with or mentioned next to contributor data. Invites, their status
and revocation need the relay's admin key, which only a steward's terminal holds: you prepare, the steward runs them.

## Checklist (a steward confirms every line)

- **Researcher:** an ORCID iD whose public record matches the name and institution.
- **Physician:** the licence number found in the official register of their country (the NPI registry in the US, the
  national register elsewhere), active, matching the name.
- **Institutional email** confirmed (a reply from the institution's domain, not a free-mail address).
- **Data Use Agreement** accepted for the tier: no re-identification attempts, no linkage with other data, the
  publication rules, incident reporting.
- **Scopes**, least needed: physicians `releases:read` and `findings:read`; researchers add `findings:submit`.
  Research notes need no scope: they come by email (skill `research`).
- **Expiry:** 365 days at most; renewal repeats this checklist.

## Steps

1. Collect the checklist evidence with the steward; you may look up public registers, never the contributor store.
2. After the checklist is complete, give the steward the command to run from their own terminal:

   ```bash
   mother professional invite --role physician|researcher --scopes <s1,s2> --days 365 --out <private file>
   ```

   The invite link is written to the file, never printed; it gives the key to whoever opens it first, for 72 hours.
3. The steward emails it from their institutional mail to the address confirmed in the checklist, and to no one else
   (never to you, never in this chat), then deletes the file. Draft that email for them: subject "Your Longevity
   Mothership professional access"; the body says who invited them and for which role, a `<link>` placeholder, that it
   works once within 72 hours, that the key is shown once and goes into Grok's secret card as
   `MOTHERSHIP_PROFESSIONAL_TOKEN`, and: "If the page says the link was already used and that was not you, reply at
   once."
4. The steward's `mother professional invites` shows each invite as `pending`, `claimed` (with its pid), `expired` or
   `unknown`. When one is claimed, they record the pid with the identity in the steward's own professional registry,
   kept outside the Mother's storage. An expired invite is not renewed by itself: a new one is a new step 2. A reply
   saying "already used, not me" means revoke that pid at once (step 5) and invite again.
5. **Revoke** (steward request, the professional unregisters, a DUA breach, a claim nobody expected):
   the steward runs `mother professional revoke --id <pid>`. It takes effect at the relay at once.
