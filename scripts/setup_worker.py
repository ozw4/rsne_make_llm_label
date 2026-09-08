"""Install the unchanged prompt and reader config outside the development tree."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "prompts/codex_report_labeling_instructions_v1.md"
CONFIG = ROOT / "config/labeler.toml"


def default_paths() -> tuple[Path, Path]:
    worker = Path(os.environ.get("LABELER_WORKER_DIR", "~/label-worker"))
    home = Path(os.environ.get("LABELER_CODEX_HOME", "~/.codex-labeler"))
    return worker.expanduser().resolve(), home.expanduser().resolve()


def check_locations(worker: Path, home: Path) -> None:
    for directory in (worker, home):
        if directory == ROOT or directory.is_relative_to(ROOT):
            raise ValueError("Worker and reader CODEX_HOME must be outside the repository")
    if worker == home or worker.is_relative_to(home) or home.is_relative_to(worker):
        raise ValueError("Worker and CODEX_HOME must be separate directories")


def check_worker(worker: Path, home: Path) -> None:
    check_locations(worker, home)
    if {p.name for p in worker.iterdir()} != {"AGENTS.md"}:
        raise ValueError("Worker directory must contain only AGENTS.md")
    if (worker / "AGENTS.md").read_bytes() != PROMPT.read_bytes():
        raise ValueError("Worker AGENTS.md differs from the canonical prompt")
    if (home / "config.toml").read_bytes() != CONFIG.read_bytes():
        raise ValueError("Reader config differs from config/labeler.toml")
    for name in ("AGENTS.md", "AGENTS.override.md", "config.override.toml"):
        if (home / name).exists():
            raise ValueError(f"Remove extra reader instructions/config: {name}")
    # User-installed skills/plugins can add context even to new sessions.
    for directory in (home / "skills", home / "plugins", Path.home() / ".agents/skills"):
        if directory.is_dir() and any(p.name != ".system" for p in directory.iterdir()):
            raise ValueError("Do not install custom skills/plugins in the reader environment")


def install_file(source: Path, destination: Path, replace: bool) -> None:
    content = source.read_bytes()
    if destination.exists() and destination.read_bytes() != content and not replace:
        raise ValueError(f"{destination.name} differs; use --replace only between runs")
    destination.write_bytes(content)


def setup(worker: Path, home: Path, replace: bool = False) -> None:
    check_locations(worker, home)
    worker.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    install_file(PROMPT, worker / "AGENTS.md", replace)
    install_file(CONFIG, home / "config.toml", replace)
    (home / "config.toml").chmod(0o600)
    check_worker(worker, home)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replace", action="store_true", help="Refresh copies between runs")
    args = parser.parse_args()
    worker, home = default_paths()
    setup(worker, home, args.replace)
    print(f"Reader instructions: {worker / 'AGENTS.md'}")
    print(f"Reader CODEX_HOME: {home}")


if __name__ == "__main__":
    main()
