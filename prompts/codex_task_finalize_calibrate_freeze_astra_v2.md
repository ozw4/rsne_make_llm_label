# Finalize, calibrate, and freeze the Astra V2 report-label artifact

This is a task for the development/maintenance Codex in
`/workspaces/rsne_make_llm_label`. It is not a report-reading prompt. Never copy
this document into the reader's `AGENTS.md` or send it to the reader.

Execute the six phases below. Implement and validate the required local tooling,
then perform the authorized operations. Preserve completed work if blocked and
report the exact unmet condition. Do not stop merely to request permission that
this task already grants. Do not bypass environment approval requirements.

## Objective and authorization

Create a self-contained, auditable artifact for all 4,407 source studies at:

`outputs/astra_support_v2_canonical_v1`

The task explicitly authorizes:

- At most ONE additional report-reading inference: a fresh session for the first
  study identified below, using `gpt-6-astra`, reasoning effort `high`, unchanged
  V2 instructions, schema, validator, and final reader configuration.
- A narrowly scoped, documented adjudication of state/support disagreements
  among byte-identical reports under the comprehensive-normal policy below.
- Copying and integrating existing artifacts without modifying their sources.
- Post-generation evaluation against the designated 58 gold studies and creation
  of separate probability/weight recommendations. No image-model training,
  image inference, Kaggle submission, or edits to existing training labels.
- Local freeze and ZIP creation after all required gates pass. No uploading,
  publishing, or sending files to another person or service.

The earlier report-278 retry instruction is historical context, not authorization
to retry report 278 or launch another remaining-reports run in this task.

Read the development `AGENTS.md` and relevant current-tree code first. Use only
this working tree for task data and source code; do not inspect the original
training repository, other branches, Git history, or unrelated archives. Use the
existing dedicated worker and CODEX_HOME only as required by the established
reader setup. Do not create subagents or reviewer-model calls.

Keep report text, gold data, labels, logs, audit tables, and generated artifacts
under ignored `data/` or `outputs/`. Never display credentials. Code, synthetic
tests, and documentation may be tracked; do not commit automatically.

## Inputs and preflight

Required source reports: `data/reports.jsonl`.

Existing output roots, all strictly read-only:

1. `outputs/pilot50_support_v2` — expected 1 accepted study.
2. `outputs/pilot50_support_v2_cont1` — expected 49 accepted studies.
3. `outputs/astra_remaining_support_v2` — expected 4,357 accepted studies.

First study to replace:

`1.2.826.0.1.3680043.8.498.10004873229099053869093324292195817260`

Optional audit references, only if supplied inside the working tree:
`astra_duplicate_report_conflicts.csv`,
`astra_evidence_consistency_candidates.csv`, and `astra_label_review.md`.
Their counts and conclusions are hypotheses to verify, not authoritative input
for changing labels. Do not require these files when the underlying data suffice.

Gold input: use `RSNA_GOLD_CSV` if explicitly set; otherwise use `data/train.csv`.
Resolve the path and require it to remain inside this working tree, including
after symlink resolution. For example, the user may stage the file themselves at
`data/train.csv` and set:

```bash
export RSNA_GOLD_CSV=/workspaces/rsne_make_llm_label/data/train.csv
```

Do not follow an external path into the original training repository. If an
explicit gold path is invalid, report it rather than silently choosing another
file. Missing/unusable gold does not prevent Phases 1–4; it blocks calibration
and freeze. Before Phase 5, check only gold availability, not its label values.

Inventory all source files and hash their original bytes. Verify unique source
IDs and exact expected coverage before spending inference quota. Do not derive
completion solely from summaries. Record counts that differ from expectations
and stop on missing/corrupt required generation artifacts.

Inspect `scripts/run_labels.py`, `scripts/setup_worker.py`,
`config/labeler.toml`, `config/codex-version.txt`,
`schemas/annotation.schema.json`, and both prompt files. Record their hashes.
Do not modify the runner, setup, schema, V1, V2, or reader configuration for this
task. Put new audit/calibration code in separate, small modules. Reuse existing
strict decoding, annotation validation, and event validation functions without
loosening any checks. Do not upgrade the CLI or substitute a model.

Check that no labeler is active. Verify final run identities against the 49-study
and 4,357-study manifests. Distinguish source-specific input hashes/paths from
generation settings. Stop if the final model, effort, CLI, prompt, schema,
runner, setup, or reader configuration cannot be reconciled. Preserve the
historical first-study configuration difference in provenance.

If the canonical destination exists, inspect it before writing. Never overwrite
a frozen artifact. Resume an unfinished build only when its recorded inputs,
settings, and task policy match; otherwise stop and report the collision.

## Phase 1 — Replace the first study with one new reader session

Create a separate run root:

`outputs/astra_support_v2_first_study_rerun_v1`

Extract exactly the designated study into an ignored UTF-8 JSONL input containing
only `study_id` and `report`. Preserve the decoded report string exactly; do not
normalize whitespace, Unicode, punctuation, or line endings within that string.

Verify the installed worker `AGENTS.md` is a byte-identical copy of V2 and that
the dedicated CODEX_HOME and working directory satisfy existing guards. Both
plugins and apps must be explicitly disabled, alongside the existing tools,
memory, and multi-agent restrictions. Refresh worker copies through the existing
setup only if needed and no reader is active. Do not copy development
instructions, gold, existing labels, or this task document into the reader.

Use the existing runner with the single-record input, `--model gpt-6-astra`,
`--reasoning-effort high`, `--attempts 1`, and `--limit 1`. Preserve the timeout
and other execution settings documented for the final accepted run; do not
invent a different timeout. Run its dry-run/preflight first. Dry-run must not
launch report inference.

The actual inference must start a new `codex exec` process with the established
dedicated environment. Its only variable stdin input is that single JSON object.
Never use resume, fork, shared report history, multiple reports, or tools in the
reader session. Keep the V2 prompt out of stdin.

Persist an append-only authorization/attempt record outside the runner's output
root before launching, so restarting this task cannot spend the one-call budget
again. If a prior attempt exists, inspect it. Reuse a fully validated success;
if it failed, was interrupted, or its launch outcome is unknown, stop without
another attempt. A crash after authorization is recorded does not grant a retry.

On inference, timeout, event, or validation failure, preserve available logs and
stop the task. Do not retry automatically, repair quotes, or substitute a label.
On success, also verify response/event/annotation agreement and a new thread ID.
Select this replacement for the canonical artifact; retain the old first study
as superseded provenance. If its labels differ, record that separately from
duplicate-report adjudication and do not pick a result using gold.

## Phase 2 — Review duplicate reports and record explicit adjudications

Use SHA-256 of each original report string encoded as UTF-8 to identify groups;
also compare the strings themselves. Never group normalized or merely similar
reports. Recompute conflicts from the selected generation results after Phase 1.

Report separately: number of duplicate groups, member studies, groups with any
annotation difference, groups with state/support differences, distinct
`(report_sha256, target)` conflicts, involved studies, and individual changed
`(study_id, target)` cells. The previous audit reported 45 groups/174 studies,
5 state/support-conflict groups/12 studies, and 17 conflicting hash-target pairs.
It estimated 22 changed individual cells under the policy below. Do not hard-code
these as findings or force the data to match them.

This task explicitly adopts the following one-time adjudication policy:

1. An unconditional statement about the entire current examination, such as
   “Normal study”, “within normal limits”, or “no significant abnormality”,
   counts as a comprehensive negative for all 12 targets when the full report
   contains no contradictory current findings. Semantically equivalent original
   language statements may qualify after reading the full report.
2. A scope-limited statement applies only to that scope. In particular, “Normal
   visualized bones and surrounding soft tissues” does not automatically negate
   menisci, ligaments, cartilage, effusion, synovitis, or Baker's cyst.
3. Read the complete original report to determine scope, timing, and conflict.
   Do not infer whole-examination normality from a substring alone. Apply V2's
   contradiction rule; do not automatically prioritize the conclusion.

Document this policy in `adjudication/policy.md`; do not inject it into or modify
the V2 generation prompt. The canonical artifact is a derived, adjudicated
version and must be identified as such.

The development agent may review these existing duplicate conflicts directly.
Do not launch further report-reading or reviewer inference, use majority voting,
or create medical regex classifiers, disease dictionaries, or automatic medical
correction rules. Produce a finite reviewed decision ledger keyed by exact report
hash, explicit study IDs, and target. A deterministic application script may
apply only these explicit decisions after checking the expected source hashes
and complete before-values. It must not infer decisions for other reports.

Limit edits to the recomputed duplicate state/support conflicts resolved by this
policy. For each changed cell, record the original cell, final cell, exact source
quotation, rationale, policy version, report hash, source annotation hash, and
review attribution. Label agent review accurately; do not claim human approval.
Use `negative/0` with a valid verbatim span when justified, or
`not_mentioned/null/[]` when there is no applicable statement. Do not turn an
unresolved contradiction into either state. Stop for clarification if a conflict
requires a new policy or broader medical adjudication. Never edit using gold.

Evidence-only wording differences need no cosmetic harmonization. Ensure no
state/support conflicts remain in the reviewed identical-report groups. Preserve
the broader same-evidence screening, if available, as unresolved review candidates
with context; do not classify all such differences as errors or silently fix
them. A newly discovered material issue outside this authorization blocks freeze
pending resolution, rather than authorizing more inference or broader edits.

## Phase 3 — Assemble the canonical artifact with honest provenance

Create this layout (additional necessary validation files are allowed):

```text
outputs/astra_support_v2_canonical_v1/
  annotations/                 # 4,407 selected/adjudicated annotations
  receipts/                    # 4,407 derived validation receipts
  generation_sources/          # Byte-preserved source runs and replacement run
  source/reports.jsonl         # Exact source-file copy, kept private
  contracts/                  # Byte-preserved prompt, schema, config, task policy
  adjudication/policy.md
  adjudication/decisions.jsonl
  adjudication/changes.jsonl
  audit/
  calibration/
  provenance.json
  labeling_summary.json
  manifest.json
  FROZEN                      # Written only in Phase 6
```

Preserve source-root names under `generation_sources/` so original receipt-relative
attempt paths remain resolvable. Copy original manifests, annotations, receipts,
responses, events, failure/interruption logs, and relevant summaries unchanged.
Do not copy credentials, worker homes, caches, symlinks, or unrelated files.
Select replacement 1 + existing 49 + existing 4,357 = 4,407 unique studies.
The superseded original first study remains in provenance but not the selected
cohort. Fail on any unexplained duplicate or missing ID.

Unchanged selected annotation files should remain byte-identical copies. Apply
only ledger-approved changes to canonical copies, retaining the schema and key
order. Original generation receipts always retain their original hashes and
thread IDs. Do not rewrite them to make an edited annotation appear model-generated.

Canonical receipts use an explicitly documented derived-receipt format distinct
from runner completion receipts. Link the canonical annotation/report hashes to
the selected original annotation, original receipt, run identity, and any
adjudication decisions. Mark whether each annotation was copied or adjudicated.
Do not claim an edited annotation equals the original response/event, and do not
invent a new generation thread, token usage, or run identity for adjudication.
Never pass derived receipts to the runner as if they were its completion markers.

Record original configuration heterogeneity and supersession, plus the settings
of the selected generation cohort. A homogeneous selected cohort does not erase
historical heterogeneity or make every canonical annotation a raw V2 response.

## Phase 4 — Independently validate all 4,407 studies against source text

Generate machine-readable and concise human-readable audit reports. Require:

- Exactly 4,407 unique source IDs, selected annotations, and derived receipts;
  exact set equality and 52,884 target cells in schema order.
- Original reports-file SHA-256 and every report-string SHA-256 recomputed from
  source, matching the relevant generation and canonical records.
- Strict JSON decoding, matching input IDs, exactly 12 targets and exact fields,
  valid states/levels/combinations, required evidence emptiness/nonemptiness, and
  exact decoded substring matching against the original report for every span.
  No normalization, fuzzy matching, translation, or quote repair.
- For all accepted generation artifacts (including the superseded original):
  original receipt annotation-byte hashes, parsed annotation/response/final
  agent-message agreement, one fresh thread and one completed turn, valid usage,
  and no unexpected tools/plans. Inspect final messages explicitly; the existing
  event summary alone does not establish message-content equality.
- Unique thread IDs across accepted sessions and all recorded attempts that have
  a thread ID. Missing thread/usage data in historical interrupted attempts must
  be disclosed; do not invent it or require a failed attempt to look completed.
- Complete provenance for every canonical change and a checked original
  generation result behind every canonical annotation. Reapply the decision
  ledger to verify the final values and ensure no unlisted edits occurred.
- No unresolved duplicate state/support conflicts; separately counted
  evidence-only differences and out-of-scope screening candidates.
- All original source-tree file hashes unchanged from preflight.

Recompute distributions and usage, including known failed and superseded attempts
once each. Cached input is part of input, not additive. Report uncached input as
input minus cached input. Identify which elapsed time is measured versus summed.
Retain `usage_accounting_complete: false` if historical attempts lack usage;
known usage is a lower bound. This documented historical limitation alone does
not block freeze when accepted results and their provenance are fully validated.

Any unexplained accepted-artifact failure blocks calibration and freeze. Do not
rerun reports or repair historical evidence to make the audit pass.

## Phase 5 — Evaluate gold 58 and calibrate separate derived recommendations

Begin reading gold values only after canonical adjudication is locked and Phases
1–4 pass. Keep evaluation code separate from generation/adjudication code. Gold
must never reach the reader or change canonical state/support/evidence.

Validate the gold file's schema, unique IDs, target mapping, binary values, and
missingness. Identify the designated 58-study evaluation cohort using explicit
local provenance or a supplied cohort list, not row position or convenient
nonmissing values. If the local data unambiguously contain exactly these 58
complete matched gold studies, record that derivation. Otherwise stop Phase 5
and request the missing cohort specification. Do not assume every row of a file
named `train.csv` is gold or silently evaluate a different cohort.

Require all 58 IDs to match source reports and have all 12 gold targets. Record
file and cohort hashes and the target-column mapping. Do not silently drop rows,
duplicate IDs, impute missing labels, or use image predictions as gold.

Produce the following with denominators and explicit `null` for undefined metrics:

- Per-target and pooled counts, observed gold positive rates by support level,
  and separate `not_mentioned` counts/rates. Include 95% Wilson intervals for
  each level/target rate; disclose within-study dependence for pooled cells.
- Per-target and pooled confusion matrices, sensitivity, specificity, precision,
  F1, balanced accuracy, and coverage for thresholds `level >= 1, 2, 3, 4`.
  Exclude null levels from threshold metrics and report that coverage explicitly.
- An additional positive-versus-negative evaluation restricted to levels 4 and
  0, with abstention/coverage accounting for uncertain and unmentioned cells.

Compare these two pooled probability mappings; do not fit target-specific models:

```text
fixed: 0 -> 0.05, 1 -> 0.25, 2 -> 0.50, 3 -> 0.75, 4 -> 0.95
empirical: prior-smoothed, monotone estimates from the gold cohort
```

Predeclare the following constants in a calibration specification before scoring;
do not tune them against the observed winner:

- For level k, use prior strength 10 with prior mean equal to fixed[k]. Compute
  `(positive_count[k] + 10 * fixed[k]) / (count[k] + 10)` and perform weighted
  isotonic regression over levels 0–4 with weights `count[k] + 10` (PAVA suffices).
- Leave one study out across all 58 studies. Fit counts, smoothing, and isotonic
  mapping exclusively on each fold's training studies. Predict all non-null
  cells of the held-out study. Clip probabilities to `[1e-6, 1 - 1e-6]` for log
  loss. Use identical held-out cells for both mappings.
- Report pooled and per-target out-of-fold Brier score and log loss, as well as
  a study-balanced mean to expose coverage differences. Select empirical only
  when pooled Brier improves by at least 0.005 and pooled log loss also improves;
  otherwise retain fixed. This is a predeclared practical rule, not a claim of
  statistical significance or an unbiased evaluation of the selected model.
- Check exact-report duplicates in gold. If present, also run leave-one-report-
  hash-group-out validation and require the same improvement rule there before
  selecting empirical. Disclose dependence and the limited sample size.
- Refit a selected empirical mapping on all 58 only after out-of-fold comparison.
  Retain fold assignments, counts, predictions, constants, and both mappings so
  the comparison can be reproduced. Empty levels use the stated prior.

Write separate derived records for every study/target, without altering annotation
JSON. Include `study_id`, target, state, support_level, `soft_target`,
`training_weight`, `training_eligible`, mapping version, and provenance links.

For a non-null level k, use the selected mapping probability p and the following
predeclared heuristic weight, where n[k] is the full gold cohort's observed count
of non-null cells at that level:

```text
soft_target = p
base_weight = abs(2 * p - 1) * n[k] / (n[k] + 10)
training_eligible = study_id not in the designated gold cohort
training_weight = base_weight if training_eligible else 0.0
```

For `not_mentioned/null`, always use `soft_target = 0.5` and
`training_weight = 0.0`. The value 0.5 is a storage placeholder with zero signal.
All 58 gold studies have `training_eligible = false` and zero training weight.
Document that a non-null level may also receive zero weight under this formula.
These weights are heuristic recommendations, not empirically validated optimal
training weights. Do not run training to choose them. Disclose any report-hash
overlap between gold and eligible studies; no image-performance claim is made.

State that evaluation measures agreement with image-derived gold. Neither
structural validation nor calibration establishes medical accuracy, and the
58-study results do not guarantee performance on the remaining cohort.

If gold/cohort data are missing or invalid, leave Phases 1–4 outputs intact with
an explicit incomplete status. Do not create `FROZEN` or a final release ZIP.

## Phase 6 — Freeze only after all required gates pass

Use synthetic reports and mocked calls for meaningful tests of new functionality:
rejected non-verbatim evidence, exact before-value guarded adjudication, immutable
source copying, derived receipt provenance, stop/no-retry across restarts, cohort
validation, fold leakage prevention, monotonic calibration, null/gold weights,
and freeze refusal on an unmet gate. Do not spend real inference on tests.

Run the repository's offline tests and syntax checks, plus the complete real-data
audit. Do not assert an old test count; report the actual result. Ensure that
scripted calibration can be regenerated from the saved inputs/specification and
that every path needed to verify the artifact is self-contained.

Produce final summaries and provenance before freezing. Include the gold cohort
snapshot used for evaluation under ignored `calibration/`, plus relevant source
code snapshots/specifications for reproduction; do not include unrelated gold
rows or training data. Preserve hashes of the original input CSV separately.

Write `manifest.json` with relative payload paths, byte sizes, SHA-256 hashes,
counts, selected generation settings, source identities, policy/calibration
versions, tests, audit status, and known limitations. Explicitly exclude the
manifest itself and `FROZEN` from its payload hash list to avoid circular hashes.
Validate the payload inventory before writing `FROZEN` last, containing the
manifest SHA-256, UTC freeze time, artifact version, and passed gate references.
After writing `FROZEN`, make no further changes inside the artifact.

Create these sibling files only after freeze gates pass:

```text
outputs/astra_support_v2_canonical_v1.zip
outputs/astra_support_v2_canonical_v1.zip.sha256
```

Never include the ZIP inside itself. Verify ZIP CRC, unique/safe relative paths,
and every member against frozen file hashes. Verify the ZIP's external SHA-256.
Do not overwrite an existing frozen artifact or ZIP; verify and reuse an exact
completed build, or report the conflict. If packaging fails, retain the frozen
directory and report packaging as incomplete; do not claim delivery is complete.

## Final response

Report concisely, in Japanese:

- Which phases passed, and whether the artifact is frozen or blocked.
- First-study inference count/outcome and whether a prior success was reused.
- Final study/cell counts, duplicate hash-target conflict count, actual changed
  study-target cell count, and remaining review limitations.
- Source immutability, strict evidence audit, and session/provenance results.
- Gold cohort size, out-of-fold mapping comparison, selected mapping, null/gold
  exclusion behavior, and the heuristic status of weights.
- Actual tests, known token usage/missing usage, and any unmet condition.
- Clickable paths to the artifact, audit, change ledger, calibration report,
  manifest, ZIP, and checksum, only when they exist.

Never claim checks that were not run, invent unavailable original files, or claim
that a freeze certifies medical correctness or image-model performance.
