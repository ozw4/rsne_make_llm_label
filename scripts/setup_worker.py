"""Install the unchanged prompt and reader config outside the development tree."""
from __future__ import annotations

import argparse
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "prompts/codex_report_labeling_instructions_v2.md"
CONFIG = ROOT / "config/labeler.toml"


def check_plugin_cache(home: Path) -> None:
    config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    if any(config.get("features", {}).get(name) is not False for name in ("plugins", "apps")):
        raise ValueError("Reader plugin and app features must be explicitly disabled")
    if any(config.get(name) for name in ("mcp_servers", "plugins", "apps")):
        raise ValueError("Do not configure reader MCP servers, plugins, or apps")
    plugins = home / "plugins"
    if not plugins.exists() and not plugins.is_symlink():
        return
    # These are CLI-generated paths observed with 0.153.4. Cached packages may
    # contain skills/code; they are allowed only with both capabilities disabled.
    boundaries = (
        (plugins, {"cache", ".remote-plugin-install-staging"}),
        (plugins / "cache", {"openai-curated-remote"}),
        (plugins / ".remote-plugin-install-staging", set()),
    )
    for directory, allowed in boundaries:
        if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
            raise ValueError("Unexpected reader plugin cache path type")
        if directory.is_dir() and any(p.name not in allowed or not p.is_dir() for p in directory.iterdir()):
            raise ValueError("Unapproved reader plugin cache entry")
    if any(p.is_symlink() for p in plugins.rglob("*")):
        raise ValueError("Reader plugin cache must not contain symbolic links")


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
    # Standalone user skills can add context even when plugins are disabled.
    for directory in (home / "skills", Path.home() / ".agents/skills"):
        if directory.is_dir() and any(p.name != ".system" for p in directory.iterdir()):
            raise ValueError("Do not install custom skills/plugins in the reader environment")
    check_plugin_cache(home)


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
