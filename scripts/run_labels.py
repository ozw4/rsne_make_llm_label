"""Sequential report labeling: one report, one new codex exec process."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from setup_worker import CONFIG, PROMPT, ROOT, check_worker, default_paths

SCHEMA = ROOT / "schemas/annotation.schema.json"
SUPPORT_LEVELS = {
    "positive": {4}, "negative": {0}, "uncertain": {1, 2, 3}, "not_mentioned": {None},
}
TOKEN_KEYS = ("input_tokens", "cached_input_tokens", "output_tokens")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def decode(text: str) -> Any:
    return json.loads(text, object_pairs_hook=no_duplicate_keys)


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as f:
        temporary = Path(f.name)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_reports(path: Path) -> list[dict[str, str]]:
    reports, seen = [], set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = decode(line)
        if not isinstance(row, dict) or set(row) != {"study_id", "report"}:
            raise ValueError(f"Line {number}: only study_id and report are allowed")
        study_id, report = row["study_id"], row["report"]
        if not isinstance(study_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,179}", study_id):
            raise ValueError(f"Line {number}: invalid filename-safe study_id")
        if study_id in seen:
            raise ValueError(f"Line {number}: duplicate study_id")
        if not isinstance(report, str) or not report.strip():
            raise ValueError(f"Line {number}: report must be nonempty text")
        seen.add(study_id)
        reports.append({"study_id": study_id, "report": report})
    if not reports:
        raise ValueError("Input contains no reports")
    return reports


def validate_annotation(value: Any, report: dict[str, str], labels: list[str]) -> None:
    if not isinstance(value, dict) or set(value) != {"study_id", "labels"}:
        raise ValueError("Invalid annotation fields")
    if value["study_id"] != report["study_id"]:
        raise ValueError("Annotation study_id mismatch")
    cells = value["labels"]
    if not isinstance(cells, dict) or list(cells) != labels:
        raise ValueError("Annotation must contain the twelve labels in schema order")
    for label in labels:
        cell = cells[label]
        if not isinstance(cell, dict) or set(cell) != {"state", "support_level", "evidence"}:
            raise ValueError(f"{label}: invalid fields")
        state, support_level, evidence = cell["state"], cell["support_level"], cell["evidence"]
        if not isinstance(state, str) or state not in SUPPORT_LEVELS:
            raise ValueError(f"{label}: invalid state")
        if support_level is not None and type(support_level) is not int:
            raise ValueError(f"{label}: support_level must be an integer or null")
        # Enforce the state/level relationship here; the schema constrains individual fields.
        if support_level not in SUPPORT_LEVELS[state]:
            raise ValueError(f"{label}: support_level conflicts with state")
        if not isinstance(evidence, list):
            raise ValueError(f"{label}: evidence must be an array")
        if (state == "not_mentioned") != (len(evidence) == 0):
            raise ValueError(f"{label}: evidence count conflicts with state")
        if any(not isinstance(s, str) or not s.strip() or s not in report["report"] for s in evidence):
            raise ValueError(f"{label}: evidence is not a nonempty verbatim substring")


def summarize_events(path: Path) -> dict:
    events = [decode(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(not isinstance(e, dict) for e in events):
        raise ValueError("Invalid event object")
    threads = [e.get("thread_id") for e in events if e.get("type") == "thread.started"]
    turns = [e for e in events if e.get("type") == "turn.completed"]
    if len(threads) != 1 or not isinstance(threads[0], str) or not threads[0] or len(turns) != 1:
        raise ValueError("Expected exactly one new thread and one completed turn")
    if any(e.get("type") == "turn.failed" for e in events):
        raise ValueError("Codex reported a failed turn")
    for event in events:
        if event.get("type", "").startswith("item."):
            kind = event.get("item", {}).get("type")
            if kind not in {"agent_message", "reasoning"}:
                raise ValueError("Unexpected tool/plan activity; no result accepted")
    usage = turns[0].get("usage", {})
    for key in TOKEN_KEYS:
        if type(usage.get(key)) is not int or usage[key] < 0:
            raise ValueError("Missing or invalid token usage")
    if usage["cached_input_tokens"] > usage["input_tokens"]:
        raise ValueError("Cached input exceeds total input")
    return {"thread_id": threads[0], "usage": usage}


def command(model: str, effort: str, worker: Path, result: Path) -> list[str]:
    # Never use resume/fork or pass the common instructions again through stdin.
    return [
        "codex", "exec", "--cd", str(worker), "--model", model,
        "--config", f'model_reasoning_effort="{effort}"',
        "--skip-git-repo-check", "--ephemeral", "--json",
        "--output-schema", str(SCHEMA), "--output-last-message", str(result), "-",
    ]


def run_identity(args: argparse.Namespace, worker: Path, home: Path) -> dict:
    installed = subprocess.run(["codex", "--version"], check=True, capture_output=True, text=True).stdout.strip()
    expected = "codex-cli " + (ROOT / "config/codex-version.txt").read_text().strip()
    if installed != expected:
        raise ValueError(f"CLI version mismatch; expected {expected}")
    return {
        "schema_version": 1, "model": args.model, "reasoning_effort": args.reasoning_effort,
        "codex_version": installed, "worker_dir": str(worker), "codex_home": str(home),
        "prompt_sha256": sha256(PROMPT.read_bytes()), "schema_sha256": sha256(SCHEMA.read_bytes()),
        "config_sha256": sha256(CONFIG.read_bytes()),
        "runner_sha256": sha256(Path(__file__).read_bytes()),
        "setup_sha256": sha256((ROOT / "scripts/setup_worker.py").read_bytes()),
        "new_session_per_report": True,
    }


def ensure_manifest(output: Path, identity: dict) -> None:
    path = output / "manifest.json"
    if path.exists():
        if decode(path.read_text(encoding="utf-8")) != identity:
            raise ValueError("Run identity changed; choose a new output directory")
    elif any(p.name != ".lock" for p in output.iterdir()):
        raise ValueError("Output directory has files but no run manifest")
    else:
        atomic_json(path, identity)


def completed(output: Path, report: dict[str, str], labels: list[str]) -> bool:
    receipt_path = output / "receipts" / f"{report['study_id']}.json"
    result_path = output / "annotations" / f"{report['study_id']}.json"
    if not receipt_path.exists():
        return False
    receipt = decode(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("report_sha256") != sha256(report["report"].encode("utf-8")):
        raise ValueError("Report changed for a completed study; use a new output directory")
    if receipt.get("status") != "complete" or receipt.get("annotation_sha256") != sha256(result_path.read_bytes()):
        raise ValueError("Completed result is incomplete or corrupted")
    validate_annotation(decode(result_path.read_text(encoding="utf-8")), report, labels)
    return True


def execute(argv: list[str], *, input: str, cwd: Path, env: dict,
            stdout: Any, stderr: Any, timeout: int) -> None:
    """Kill the complete CLI process group on timeout or interruption."""
    process = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
        text=True, encoding="utf-8", cwd=cwd, env=env, start_new_session=True,
    )
    try:
        process.communicate(input=input, timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, argv)



def label_one(report: dict[str, str], args: argparse.Namespace, worker: Path, home: Path,
              output: Path, labels: list[str]) -> None:
    parent = output / "attempts" / report["study_id"]
    parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(args.attempts):
        check_worker(worker, home)
        attempt_dir = Path(tempfile.mkdtemp(prefix="attempt-", dir=parent))
        result = attempt_dir / "response.json"
        events = attempt_dir / "events.jsonl"
        stderr = attempt_dir / "stderr.log"
        env = dict(os.environ, CODEX_HOME=str(home))
        # CLI authentication uses the dedicated home; accidental API credentials are not inherited.
        for name in ("CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
            env.pop(name, None)
        started = time.monotonic()
        try:
            with events.open("w", encoding="utf-8") as out, stderr.open("w", encoding="utf-8") as err:
                execute(
                    command(args.model, args.reasoning_effort, worker, result),
                    input=json.dumps(report, ensure_ascii=False),
                    cwd=worker, env=env, stdout=out, stderr=err, timeout=args.timeout,
                )
            metadata = summarize_events(events)
            value = decode(result.read_text(encoding="utf-8"))
            validate_annotation(value, report, labels)
            annotation = output / "annotations" / f"{report['study_id']}.json"
            atomic_json(annotation, value)
            receipt = {
                "status": "complete", "report_sha256": sha256(report["report"].encode("utf-8")),
                "annotation_sha256": sha256(annotation.read_bytes()),
                "attempt_dir": str(attempt_dir.relative_to(output)),
                "elapsed_seconds": round(time.monotonic() - started, 3), **metadata,
            }
            # The receipt is the completion marker; publish it only after full validation.
            atomic_json(output / "receipts" / f"{report['study_id']}.json", receipt)
            return
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            atomic_json(attempt_dir / "failure.json", {
                "status": "failed", "error_type": type(exc).__name__,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            })
            if attempt + 1 == args.attempts:
                raise RuntimeError(f"Labeling failed; inspect private logs in {attempt_dir}") from exc
            time.sleep(5)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("Must be positive")
    return number


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="JSONL with only study_id and report")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", required=True, help="Explicit model ID available to your Codex account")
    parser.add_argument("--reasoning-effort", choices=["minimal", "low", "medium", "high", "xhigh"], default="medium")
    parser.add_argument("--limit", type=positive_int, help="Use the first N records, including already completed ones")
    parser.add_argument("--attempts", type=positive_int, default=1, help="Every attempt starts a new session")
    parser.add_argument("--timeout", type=positive_int, default=300, help="Seconds allowed per attempt")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs/config without running Codex")
    return parser.parse_args()


def run(args: argparse.Namespace) -> None:
    worker, home = default_paths()
    check_worker(worker, home)
    reports = read_reports(args.input)
    labels = list(decode(SCHEMA.read_text(encoding="utf-8"))["properties"]["labels"]["properties"])
    selected = reports[:args.limit]
    if args.dry_run:
        print(json.dumps({"input_records": len(reports), "selected": len(selected), "codex_called": False}))
        return
    output = args.output_dir.expanduser().resolve()
    if output == worker or output.is_relative_to(worker) or output == home or output.is_relative_to(home):
        raise ValueError("Outputs must not be stored in the reader environment")
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ensure_manifest(output, run_identity(args, worker, home))
        skipped = 0
        for index, report in enumerate(selected, 1):
            if completed(output, report, labels):
                skipped += 1
            else:
                label_one(report, args, worker, home, output, labels)
            print(json.dumps({"processed": index, "selected": len(selected), "skipped": skipped}), flush=True)


def main() -> None:
    os.umask(0o077)
    try:
        run(parse_args())
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("Interrupted; verified results remain resumable.", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
