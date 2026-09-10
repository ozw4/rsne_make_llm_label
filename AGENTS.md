# RSNA report labeler: development instructions

This working tree is dedicated to running Codex to read knee MRI reports one at a time.
This file is for Codex when implementing and maintaining the execution scripts, validation, and devcontainer. It does not contain instructions for labeling reports.

## Scope

Use only files in the current working tree. Do not explore the original training repository, other branches, history, or archives.
Image model training and inference, Kaggle submissions, and changes to existing training labels are out of scope.
Keep report text, generated labels, execution logs, and credentials out of Git. Do not display credentials.

## Report-reading contract

The canonical labeling instructions are in `prompts/codex_report_labeling_instructions_v2.md`.
Do not change the official definitions, four states, 12 targets, support-level mapping, or output contract for verbatim evidence without an explicit request. Keep the v1 prompt unchanged when maintaining v2.
Copy the labeling instructions unchanged into `AGENTS.md` in the dedicated report-reading working directory so that they are loaded automatically. Do not also include them in standard input.
Launch the report-reading Codex process with a working directory and dedicated CODEX_HOME that prevent it from loading this development AGENTS.md.

Start a new `codex exec` process for every report and every retry.
Do not implement `resume`, `fork`, subagents, inheritance of history or memory from previous reports, or submission of multiple reports to one session.
The only variable input must be a single JSON object containing `study_id` and `report`. Do not pass existing labels, gold labels, or image predictions.
The model must only read the report and return a JSON response. Do not delegate file exploration, saving, or external searches to it.

## Script responsibilities

Limit scripts to retrieving input, launching Codex, validating the output format, saving results, recording usage, and rerunning unfinished reports.
Validation must check the output contract, including matching the input ID, the 12 keys, allowed states and support levels, their required combinations, and verbatim evidence that matches substrings of the report.
Do not create medical regex classifiers, disease-name dictionaries, or correction logic based on existing labels.
Do not replace failures with negative or not_mentioned. Save only validated results as complete.
On resumption, skip only validated results whose report text, labeling instructions, and execution settings match. Use separate output locations for different versions.

## Implementation guidelines

Keep functions small and give each a single responsibility. Use sequential processing for the initial version; do not add unnecessary abstractions or distributed processing.
Use synthetic reports and mocked Codex calls in tests so that routine tests do not incur paid executions.
