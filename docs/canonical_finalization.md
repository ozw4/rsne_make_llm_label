# Canonical V2 finalization

The development task in
[`codex_task_finalize_calibrate_freeze_astra_v2.md`](../prompts/codex_task_finalize_calibrate_freeze_astra_v2.md)
authorizes a separate derived-artifact workflow. It does not change the original
reader contract, runner, validator, schema, or generation outputs. Gold is used
only after adjudication and annotation validation, and never reaches the reader.

The scripts use the Python standard library. Run from the repository root:

```bash
# Offline generation-artifact audit and reader preflight; no inference.
python scripts/finalize_first_study.py

# Only under the task's explicit one-call authorization. A durable reservation
# blocks a second launch after failure/interruption, including after restart.
python scripts/finalize_first_study.py --execute-once

# Requires the finite, explicitly reviewed private decision ledger first.
python scripts/canonical_build.py assemble
python scripts/calibrate_support.py
python scripts/calibrate_support.py --verify
python scripts/freeze_canonical.py freeze
python scripts/freeze_canonical.py package
```

Private working records live under `outputs/astra_support_v2_finalize_work`.
The original three runs are read-only. The replacement run uses
`outputs/astra_support_v2_first_study_rerun_v1`. The canonical output is
`outputs/astra_support_v2_canonical_v1`, with a sibling ZIP and SHA-256 file.

`canonical_audit.py` validates original accepted results against source text,
receipts and events. `canonical_build.py` replays exact, hash-guarded decisions;
it contains no medical rules that generate adjudications. Original generation
receipts remain byte-preserved, while canonical receipts explicitly identify
derived values and link their generation and adjudication provenance. They are
not runner completion receipts and must not be used for runner resumption.

Calibration requires exactly 58 complete binary gold rows, with all other rows
entirely unlabeled, exact source-ID coverage and matching supplied report text.
It records the source CSV hash and saves only the matched gold cohort. Partial,
ambiguous, or malformed gold prevents freeze. The selected probability mapping
and heuristic weights are separate derived recommendations; no existing labels
or image-training data are changed.

The freeze command requires the full artifact audit, calibration reproduction,
offline tests and syntax checks to pass before writing `FROZEN`. Packaging checks
CRC, member paths, every member hash and the ZIP checksum. A frozen artifact is
never updated. Verification can run from its bundled reproduction scripts using
`python -B`, without the original gold CSV or external repositories; see the
artifact's `reproduction/README.md`.

Routine tests use synthetic data and mocks:

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
```
