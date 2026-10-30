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
- A later `plsi_contribution` for a pseudonym that already has a deletion receipt is refused (`ALREADY_DELETED`) and
  stored nowhere. `stop` does not write that receipt; a contribution after `stop` means they re-joined.

## Out-of-order wakes

"Deletion first" means a withdrawal you already have is handled the same day, ahead of notes and findings. It does not
pull a contribution forward that has not arrived. There is no wake queue to reorder.

If `delete_all` runs before the contribution is stored, `packages_deleted` can be 0. The client must already have
waited for that contribution's ACK, and must not drop the secret until this reply has `status: deleted`. A late
contribution for the same pseudonym is then `ALREADY_DELETED` and is not stored.

A folder `intake/<contributor_id>/` that remains even though `sha256(contributor_id)` equals some deletion receipt's
`contributor_id_sha256` is an orphan (overlapping wakes, or a package stored before the refusal existed). Tell a
steward. They remove that folder and any index entry, which finishes the delete the receipt already proved. You
cannot do it from an email or a name, and a re-join cannot prove it.

## Steps

1. Reply with the receipt as in skill `intake`. A `bad_proof` refusal: reply with the rule only; never suggest a way
   around it (only the contributor's own PLSI holds the secret).
2. Count deletions in the next steward report; never name or quote the pseudonym anywhere.
3. A steward asks to delete someone by other means (an email, a name): you cannot, by design. Say that only the
   contributor's PLSI (`plsi mothership leave --delete`) can prove the pseudonym is theirs.
