from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from discord_backup.bootstrap import ensure_runtime_dependencies


def _crash_log_path() -> Path:
    log_dir = Path(os.environ.get("TEMP", ".")) / "DiscordAccountBackup"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "crash.log"


def _log_crash(exc: BaseException) -> Path:
    log_path = _crash_log_path()
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{datetime.now().isoformat(timespec='seconds')}] {type(exc).__name__}: {exc}\n")
        handle.write(traceback.format_exc())
        handle.write("\n\n")
    return log_path


def main() -> None:
    ensure_runtime_dependencies(verbose=True)
    filtered_args = [arg for arg in sys.argv[1:] if arg != "--elevated"]
    if not filtered_args:
        from discord_backup.tui import run_tui

        run_tui()
    else:
        from discord_backup.cli import run as run_cli

        sys.argv = [sys.argv[0], *filtered_args]
        run_cli()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise
    except Exception as exc:  # pragma: no cover - crash guard for launcher usage
        log_path = _log_crash(exc)
        print(f"\n[error] {exc}")
        print(f"[error] Crash details written to {log_path}")
        sys.exit(1)
