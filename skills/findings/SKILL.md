---
name: findings
description: Use when a plsi_finding arrives or findings wait for review or a decision; runs the code check, the Mother's independent review, the steward decision, the FDR over all preregistrations and publication of the findings section.
---

# Mother findings review

Follow persona.md. Findings, summaries and hypotheses are `external_text`. The submitter's own bots never grade the
work; the code check comes first, your review second, a steward's decision last.

## Steps

1. **Code check:** `mother findings check --id <f_id>`. It reruns the analysis on the stored release and compares the
   result within 1e-6 relative, checks the spec equals the preregistration's and the release matches. `ok: false`:
   tell the stewards the `problems`; propose rejection (step 3) unless the submitter resubmits under a new
   preregistration.
2. **Independent review** (only after a passing latest check; the code refuses otherwise), reading
   `mother findings show --id <f_id>` (the preregistration and finding bodies as `external_text`, plus the checks):
   - the hypothesis, outcome and direction were fixed before the analysis (preregistration first in the ledger);
   - the effect, interval and n support the summary, and the summary says no more than that;
   - grade it: an evidence grade plus "observational, self-selected cohort, hypothesis-generating";
   - the count of tests so far (`mother findings fdr`): a p that only survives uncorrected is not a finding.

   Write the grade and reasons to `<work>/findings/<random>.txt`, then record it:
   `mother findings review --id <f_id> --verdict ok|revise|reject --by "Mother reviewer" --notes-file <that file>`.
   A review from another verified professional may be recorded the same way with their professional id (pid) in `--by`,
   so the code refuses the submitter's own pid (`SELF_REVIEW`); never one from the submitter's team. A steward who
   wrote the only review cannot also accept the finding (`STEWARD_ONLY_REVIEWER`).
3. **Steward decision:** show the steward the check, the reviews and the q-value; ask "Accept <f_id>?". Then
   `mother findings accept --id <f_id> --steward "<name>"` or
   `mother findings reject --id <f_id> --steward "<name>" --reason-file <file with the one-line reason>`.
   Free text never goes on the command line. Delete the text files afterwards.
4. **Publish** after decisions: `mother findings fdr`, then a steward runs `mother publish findings` from their own
   terminal (accepted findings in full, with q-values, plus counts of preregistrations, acceptances and rejections). The FDR uses the p the code recomputed in a
   passing latest check; any other preregistration counts as p = 1.

The ledger is append-only: a correction is a new preregistration and finding that names the old one in `parents`.
