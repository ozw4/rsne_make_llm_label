# rsne_make_llm_label

The separately authorized canonical finalization workflow is documented in
[Canonical V2 finalization](docs/canonical_finalization.md). It preserves the
generation contract and original artifacts, with separate adjudication provenance
and post-generation gold calibration.

This dedicated workspace reads knee MRI reports in **a new Codex session for every report** and saves the states and ordinal report support levels of 12 targets with evidence quoted from the original report. It can be run and tested using only the Python standard library. It does not train on images, load ground-truth labels, or convert results into binary 0/1 training labels.

## Layout

```text
/workspaces/rsne_make_llm_label/
├── AGENTS.md                         # Implementation and maintenance instructions
├── .devcontainer/
│   ├── devcontainer.json
│   └── post-create.sh
├── config/
│   ├── codex-version.txt              # Pins CLI version 0.153.4
│   └── labeler.toml                   # Source of truth for report-reading configuration
├── prompts/
│   ├── codex_report_labeling_instructions_v1.md  # Preserved previous contract
│   └── codex_report_labeling_instructions_v2.md  # Active labeling instructions
├── schemas/
│   └── annotation.schema.json
├── scripts/
│   ├── setup_worker.py
│   └── run_labels.py
├── tests/
│   └── test_labeler.py
├── data/                             # Excluded from Git except for its README
└── outputs/                          # Excluded from Git except for its README

/home/vscode/label-worker/
└── AGENTS.md                         # Unmodified copy of the shared v2 instructions

/home/vscode/.codex-labeler/           # Dedicated CODEX_HOME and Docker volume for report reading
├── config.toml                       # Copy of config/labeler.toml
└── credentials, etc.                 # Do not add to Git
```

Setup creates `label-worker` and the report-reading `CODEX_HOME` inside the container. There is no separate source of truth for the worker's AGENTS.md in the repository. Do not set `CODEX_HOME` globally for the development Codex; pass the dedicated value only to report-reading processes.

## Getting started

Open only this repository in VS Code and run **Dev Containers: Reopen in Container**. The container sets up Python 3.12 and Node.js 22 and installs the Codex CLI version pinned in `config/codex-version.txt`. Initial setup requires network access to download the image and npm packages. Authentication, paid inference, and data downloads do not run automatically.

Authenticate the account used for report reading in the container terminal.

```bash
CODEX_HOME="$LABELER_CODEX_HOME" codex login --device-auth
```

Credentials are stored in a dedicated volume. Do not mount the host's `~/.codex`, the original training repository, gold labels, image predictions, or image data. Before sending real data, check its terms of use and whether it may be submitted to the Codex environment you will use.

Place data in `data/reports.jsonl` after removing label columns. Use UTF-8 JSONL, with exactly two keys per line: `study_id` and `report`. Escape any line breaks in report text within the JSON string. Do not modify, summarize, or normalize the report text.

The following is a synthetic example of the input format, not real data.

```json
{"study_id":"synthetic-001","report":"The ACL is intact. Small joint effusion."}
```

The v2 pilot is limited to the first 50 reports in source order. It checks the execution pipeline and annotations; it does not guarantee that the selected reports have no gold labels and is not an accuracy evaluation. Keep the source file and ordering unchanged throughout the pilot.

Set an explicit model ID available to the dedicated report-reading account and keep it fixed for the pilot. An explicit model already configured in that dedicated environment may be reused; if neither is available, stop before paid execution. Check login status without logging in automatically or switching accounts. Stop any active labeler run before refreshing the worker instructions to v2.

```bash
export CODEX_MODEL="replace-with-an-available-model-id"
CODEX_HOME="$LABELER_CODEX_HOME" codex login status

# Only after confirming that no labeler run is active.
python scripts/setup_worker.py --replace

# Validate the first 50 reports and instruction setup without launching Codex.
python scripts/run_labels.py \
  --input data/reports.jsonl --output-dir outputs/pilot50_support_v2 \
  --model "$CODEX_MODEL" --reasoning-effort medium \
  --attempts 1 --timeout 300 --limit 50 --dry-run

# Process only the first report. This consumes usage quota.
python scripts/run_labels.py \
  --input data/reports.jsonl --output-dir outputs/pilot50_support_v2 \
  --model "$CODEX_MODEL" --reasoning-effort medium \
  --attempts 1 --timeout 300 --limit 1
```

Verify that the first report completed with a valid v2 annotation before continuing. If it fails, stop and inspect the failure; do not retry it or proceed to the next command automatically. After successful validation, use the same input, output directory, and execution settings for the first 50 reports:

```bash
python scripts/run_labels.py \
  --input data/reports.jsonl --output-dir outputs/pilot50_support_v2 \
  --model "$CODEX_MODEL" --reasoning-effort medium \
  --attempts 1 --timeout 300 --limit 50
```

`--limit N` means the first N reports, including any already completed reports. The validated first report is skipped when processing the first 50. Stop at 50 and retain the limit; this workflow does not authorize a full-dataset run. Do not retry a failed report automatically.

After the runner initializes `manifest.json`, create the private summary at `outputs/pilot50_support_v2/pilot_summary.json`, outside Git. Record the selected study IDs, input hash, completion and failure counts, state and support-level counts by target, and token usage. For a completed pilot, verify 50 valid annotations, 50 distinct receipt thread IDs, and 600 target cells. Report input, cached input, output, and total tokens; cached input is included in input and total tokens and must not be added twice. Include failed-attempt usage wherever it is available. Do not convert the results to binary labels, weights, or calibrated probabilities, use majority voting, or run reviewer LLMs.

## Execution and resume contract

The shared instructions are loaded from the worker's AGENTS.md, and standard input contains only the JSON for one report. Each invocation starts a new `codex exec --ephemeral --json --output-schema ...` process. Do not use `resume`, `fork`, history from a previous report, or multiple reports in a single input. The prompt and output format are fixed, but cache hits across new sessions are not guaranteed; check the usage records to verify them.

Defaults are sequential processing, one attempt, and a 300-second timeout per report. For this pilot, keep `--attempts 1` and stop on any failure without retrying the failed report. Failures are not replaced with negative or not-mentioned results. A timeout or Ctrl-C terminates the CLI process group.

When reusing an output directory, changes to the model, reasoning settings, CLI version, shared instructions, schema, report-reading configuration, or execution code are rejected. Processing also stops if the report text changes for a completed study. Use a new output directory for a changed version. To update the configuration, stop any active run and then update the copies with `python scripts/setup_worker.py --replace`.

## Outputs

```text
outputs/pilot50_support_v2/
├── manifest.json                     # Fixed settings and SHA-256 hashes of each file
├── pilot_summary.json                # Private summary created after manifest initialization
├── annotations/<study_id>.json       # state/support_level/evidence for the 12 targets
├── receipts/<study_id>.json          # Validated completion marker, usage, and thread ID
└── attempts/<study_id>/attempt-*/
    ├── response.json                 # Model response for each attempt (may not be generated)
    ├── events.jsonl                  # CLI JSON events
    ├── stderr.log
    └── failure.json                  # Only on failure
```

Each target has exactly three fields: `state`, `support_level`, and `evidence`. Support levels describe ordinal report evidence for meeting the official positive criteria, not disease severity, answer confidence, or calibrated probabilities.

| state | support_level | Report support |
|---|---|---|
| negative | 0 | Clearly supports absence of a qualifying finding, including a clearly subthreshold finding. |
| uncertain | 1 | Leans toward not meeting the positive criteria, but is not conclusive. |
| uncertain | 2 | Relevant information is present without a supported direction. |
| uncertain | 3 | Leans toward meeting the positive criteria, but is not conclusive. |
| positive | 4 | Clearly supports meeting the positive criteria. |
| not_mentioned | null | No applicable report statement. |

Levels 1 and 3 require directional evidence quoted from the report. Do not infer direction from general prevalence, other target labels, or missing severity information, and do not impose quotas.

Validation checks that the ID matches, all 12 keys appear in the required order, states and support levels use the allowed values and combinations, evidence arrays are empty when required, and each evidence string is a substring of the original report. Regular expressions are not used to judge or override medical correctness. A result is also not marked complete if the events reveal unexpected tool use or multiple turns.

`receipts` are written last, after saving the results. On resume, the report hash, result hash, and format are rechecked, and only completed reports are skipped. Processing stops if completed files are corrupted instead of silently overwriting them. The presence of `manifest.json` alone does not mean all reports are complete.

Usage is recorded in fields such as `usage.input_tokens`, `usage.cached_input_tokens`, and `usage.output_tokens` in each receipt. Failed attempts may also consume usage, so `events.jsonl` is retained for each attempt. Logs and evidence contain report information; do not add them to Git or share them publicly. `--ephemeral` does not delete this application's results or event logs.

## Tests

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
```

Tests use synthetic reports and mocks without making calls to Codex. Passing offline tests does not verify actual authentication, model availability, label accuracy, or cache hits. Use the bounded pilot above to check authentication, model availability, and actual usage; label accuracy requires a separately authorized evaluation.

This devcontainer separates the workspace to keep unnecessary data out. It does not provide complete isolation from malicious code. Do not add custom skills, MCP, plugins, or a global AGENTS.md to the report-reading environment.

## References

The active `codex_report_labeling_instructions_v2.md` extends the current English v1 instructions with the required ordinal `support_level` field. The v1 file remains unchanged. Official quotations, medical definitions, four states, 12 targets, and verbatim evidence rules are preserved. Medical criteria follow the sources cited in the prompt.

- [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
- [AGENTS.md](https://developers.openai.com/codex/guides/agents-md)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [Codex authentication](https://developers.openai.com/codex/auth)
- [Codex CLI 0.153.4 release](https://github.com/openai/codex/releases/tag/rust-v0.153.4)
- [Dev Containers Python image](https://github.com/devcontainers/images/tree/main/src/python)

The reader explicitly disables `features.plugins` and `features.apps`. Codex 0.153.4 can still leave downloaded packages under `plugins/cache/openai-curated-remote` and an empty `plugins/.remote-plugin-install-staging` directory. Preflight accepts this observed cache layout only while both features are disabled, rejects symbolic links and other plugin cache locations, and retains the standalone skill and extra-instruction checks. Check the effective flags with `CODEX_HOME="$LABELER_CODEX_HOME" codex features list` from the reader directory after setup.

Configuration or setup changes alter the run identity. Preserve earlier manifests and results, and use a separate output directory with an explicit input containing only the unattempted members of the original cohort. A combined private summary must retain each part's identity and any unresolved limitation on the earlier reader context; the absence of tool events alone does not prove that plugin instructions were absent.
