# Input data (local only)

Place `reports.jsonl` here. Use UTF-8 with one record per line and only the keys `study_id` and `report`.
The execution script rejects extra columns, empty reports, and duplicate IDs.
Do not summarize or normalize the original text. Escape line breaks in multiline reports within the JSON string.

Do not bring the original `train.csv`, gold labels, existing labels, image predictions, or images into this environment.
Everything in this directory except this README is excluded from Git.
