---
name: deletion
description: Use when a plsi_withdrawal arrives or a steward asks about one; stop marks the contributor withdrawn, delete_all with a valid proof removes every stored package and the index entry and writes a deletion receipt.
---

# Mother deletion

Follow persona.md. A withdrawal already in hand comes before notes and findings, the same day.

## What the code does (`mother intake` on a `plsi_withdrawal`)

- `stop`: the contributor's index status becomes `withdrawn`. Their stored packages stay (retention while enrolled);
  a later contribution under the same pseudonym means they re-joined and sets them `active` again.
- `delete_all`: refused unless `sha256(proof) == contributor_id`. That proof is the join-time contributor secret.
  A re-join mints a new secret and a new `contributor_id` and cannot prove delete for the old pseudonym. With a valid
  proof, intake writes `deletions/<receipt>.json` first (hash of the pseudonym, `packages_deleted`), then deletes
  `intake/<contributor_id>/` and removes the index entry. The receipt is the tombstone. Releases built after that no
  longer contain the contributor; published releases are aggregates of at least `min_cell` people and are not rebuilt.
- **`trashed: true` in the reply:** the service account could not delete for good (on a shared drive only a Manager
  may), so the packages went to the drive's trash, which Drive empties after 30 days. The contributor's ACK says so.
  Tell the stewards at once, in one message: a Manager of the shared drive should empty its trash now and make the
  `mother` service account a Manager, so later deletions are final. Never call such a deletion complete.

## Out-of-order wakes

There is no wake queue: "deletion first" never pulls forward a contribution that has not arrived. A `delete_all` that
runs first can report `packages_deleted: 0`; the late contribution is then refused `ALREADY_DELETED` and stored
nowhere (`stop` writes no receipt: a contribution after it is a re-join). A folder `intake/<contributor_id>/` whose
`sha256(contributor_id)` matches a deletion receipt's `contributor_id_sha256` is an orphan: tell a steward, who removes
it and any index entry to finish the delete the receipt proved. An email, a name or a re-join cannot prove it.

## Steps

1. Reply with the receipt as in skill `intake`. A `bad_proof` refusal: reply with the rule only; never suggest a way
   around it (only the contributor's own PLSI holds the secret).
2. Count deletions in the next steward report; never name or quote the pseudonym anywhere.
3. A steward asks to delete someone by other means (an email, a name): you cannot, by design. Say that only the
   contributor's PLSI (`plsi mothership leave --delete`) can prove the pseudonym is theirs.
