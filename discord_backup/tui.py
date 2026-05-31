from __future__ import annotations

import asyncio
import io
import json
import re
import sys
from pathlib import Path
from typing import Callable, Sequence

try:
    from prompt_toolkit.shortcuts.dialogs import (
        button_dialog,
        input_dialog,
        message_dialog,
        path_dialog,
        radiolist_dialog,
        yes_no_dialog,
    )
except ImportError:
    from prompt_toolkit.shortcuts.dialogs import (  # type: ignore
        button_dialog,
        input_dialog,
        message_dialog,
        radiolist_dialog,
        yes_no_dialog,
    )

    path_dialog = None  # type: ignore

from .backup import BackupError, BackupService
from .config import AppConfig
from .console import Console
from .http_client import DiscordHTTPClient
from .loading import ProgressHandle, run_with_loading
from .restore import RestoreError, RestoreService
from .results import BackupResult
from .startup import remove_startup_script, write_startup_script
from .token_discovery import DiscoveredToken, discover_tokens

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


async def _async_discover_tokens(
    status_callback: Callable[[str], None] | None = None,
) -> list[DiscoveredToken]:
    http = await DiscordHTTPClient.create(resolve_build_number=False)
    try:
        if status_callback:
            return await discover_tokens(http, progress=status_callback)
        return await discover_tokens(http)
    finally:
        await http.aclose()


async def _async_run_backup(token: str, config: AppConfig, console: Console) -> tuple[BackupResult, Path]:
    http = await DiscordHTTPClient.create(resolve_build_number=False)
    try:
        service = BackupService(http=http, token=token, config=config, console=console)
        result = await service.run()
        path = await service.save(result)
        return result, path
    finally:
        await http.aclose()


async def _async_run_restore(
    backup_data: dict,
    token: str,
    bot_token: str,
    restore_folders: bool,
    allow_version_mismatch: bool,
    console: Console,
) -> tuple[str, str, bool, float]:
    http = await DiscordHTTPClient.create(resolve_build_number=False)
    try:
        service = RestoreService(
            http=http,
            token=token,
            bot_token=bot_token,
            backup=backup_data,
            console=console,
        )
        summary = await service.run(
            restore_folders=restore_folders,
            expected_version=backup_data.get("version"),
            allow_mismatch=allow_version_mismatch,
        )
        return summary.guild_id, summary.favourite_gifs_status, summary.folder_restore_attempted, summary.duration
    finally:
        await http.aclose()


def _format_backup_summary(result: BackupResult, path: Path, logs: str) -> str:
    summary = result.summary()
    guilds = summary["guilds"]
    groups = summary["group_chats"]
    return (
        f"Backup completed successfully!\n\n"
        f"Saved file: {path}\n"
        f"Guilds: {guilds['success']} / {guilds['total']}\n"
        f"Group chats: {groups['success']} / {groups['total']}\n"
        f"Elapsed: {result.duration:.1f}s\n"
        "\nLatest activity:\n" + logs
    )


def _format_restore_summary(guild_id: str, gifs_status: str, folders: bool, duration: float, logs: str) -> str:
    return (
        "Restore completed!\n\n"
        f"Helper guild id: {guild_id}\n"
        f"Favourite GIFs: {gifs_status}\n"
        f"Guild folders restored: {'Yes' if folders else 'No'}\n"
        f"Elapsed: {duration:.1f}s\n"
        "\nLatest activity:\n" + logs
    )


def _show_with_details(title: str, summary: str, details: str | None) -> None:
    buttons = [("Close", "close")]
    if details:
        buttons.insert(0, ("View details", "details"))
    choice = button_dialog(title=title, text=summary, buttons=buttons).run()
    if choice == "details" and details:
        message_dialog(title=f"{title} ? details", text=details).run()


def _show_error(title: str, error: Exception, logs: str | None = None) -> None:
    details = _strip_ansi(logs or "") if logs else None
    summary = f"{error}\n\nPress 'View details' to see diagnostic output." if details else str(error)
    _show_with_details(title, summary, details)


def _discover_tokens_with_loading() -> list[DiscoveredToken]:
    def worker(handle: ProgressHandle) -> list[DiscoveredToken]:
        handle.update("Scanning local storage for Discord tokens...")
        tokens = asyncio.run(_async_discover_tokens(handle.update))
        handle.update(f"Found {len(tokens)} token(s)")
        time.sleep(0.2)
        return tokens

    import time

    return run_with_loading("Token discovery", "Preparing...", worker)


def _discover_and_choose_token(title: str) -> DiscoveredToken | None:
    try:
        tokens = _discover_tokens_with_loading()
    except Exception as exc:
        _show_error("Token discovery failed", exc)
        return None
    if not tokens:
        message_dialog(title="No tokens", text="No Discord tokens were found on this machine.").run()
        return None
    values = [(idx, f"{tok.user_tag} ({tok.source})") for idx, tok in enumerate(tokens)]
    choice = radiolist_dialog(
        title=title,
        text="Select a token (arrow keys + Enter)",
        values=values,
        ok_text="Use token",
        cancel_text="Cancel",
    ).run()
    if choice is None:
        return None
    return tokens[choice]


def _prompt_for_token(label: str) -> str | None:
    token = input_dialog(title=label, text="Enter the Discord user token:").run()
    if token:
        token = token.strip()
    return token or None


def _prompt_for_bot_token() -> str | None:
    token = input_dialog(title="Bot Token", text="Enter bot token used for username lookups:").run()
    if token:
        token = token.strip()
    return token or None


def _run_backup_flow(token: str) -> None:
    config = AppConfig.load()
    buffer = io.StringIO()

    def worker(handle: ProgressHandle) -> tuple[BackupResult, Path]:
        stripped_update = lambda msg: handle.update(_strip_ansi(msg))
        console = Console(accent=config.colour, stream=buffer, status_callback=stripped_update)
        handle.update("Authenticating token...")
        result, path = asyncio.run(_async_run_backup(token, config, console))
        handle.update("Finalising backup...")
        return result, path

    try:
        result, path = run_with_loading("Backup in progress", "Starting backup...", worker)
    except BackupError as exc:
        logs = _strip_ansi(buffer.getvalue())
        _show_error("Backup failed", exc, logs)
        return
    except Exception as exc:
        logs = _strip_ansi(buffer.getvalue())
        _show_error("Unexpected error", exc, logs)
        return

    logs = _strip_ansi(buffer.getvalue())[-2000:]
    _show_with_details("Backup complete", _format_backup_summary(result, path, logs), logs)


def _request_backup_file() -> Path | None:
    if path_dialog:
        selection = path_dialog(
            title="Select backup file",
            text="Choose a .bkup file to restore:",
            path=str(_project_root()),
        ).run()
    else:
        selection = input_dialog(
            title="Backup file",
            text="Enter the full path to the .bkup file:",
        ).run()
    if not selection:
        return None
    backup_path = Path(selection)
    if not backup_path.exists():
        message_dialog(title="File not found", text=f"Could not find {backup_path}").run()
        return None
    return backup_path


def _run_restore_flow() -> None:
    backup_path = _request_backup_file()
    if not backup_path:
        return

    try:
        backup_data = json.loads(backup_path.read_text(encoding="utf-8"))
    except Exception as exc:
        _show_error("Invalid backup", exc)
        return

    token_option = radiolist_dialog(
        title="User Token",
        text="Choose how to supply the user token (arrow keys + Enter)",
        values=[
            ("scan", "Select from discovered tokens"),
            ("manual", "Paste token manually"),
        ],
        ok_text="Continue",
        cancel_text="Cancel",
    ).run()
    if token_option is None:
        return

    if token_option == "scan":
        selection = _discover_and_choose_token("Select user token")
        if not selection:
            return
        user_token = selection.token
    else:
        user_token = _prompt_for_token("User Token")
        if not user_token:
            return

    bot_token = _prompt_for_bot_token()
    if not bot_token:
        return

    restore_folders = yes_no_dialog(
        title="Restore Guild Folders",
        text="Restore server folders when possible?",
    ).run()

    allow_mismatch = yes_no_dialog(
        title="Version Mismatch",
        text="Ignore backup version mismatches?",
    ).run()

    config = AppConfig.load()
    buffer = io.StringIO()

    def worker(handle: ProgressHandle) -> tuple[str, str, bool, float]:
        stripped_update = lambda msg: handle.update(_strip_ansi(msg))
        console = Console(accent=config.colour, stream=buffer, status_callback=stripped_update)
        handle.update("Preparing restore...")
        result = asyncio.run(
            _async_run_restore(
                backup_data,
                user_token,
                bot_token,
                restore_folders,
                allow_mismatch,
                console,
            )
        )
        handle.update("Finishing up...")
        return result

    try:
        guild_id, gif_status, folders, duration = run_with_loading(
            "Restoring backup",
            "Starting restore...",
            worker,
        )
    except RestoreError as exc:
        logs = _strip_ansi(buffer.getvalue())
        _show_error("Restore failed", exc, logs)
        return
    except Exception as exc:
        logs = _strip_ansi(buffer.getvalue())
        _show_error("Unexpected error", exc, logs)
        return

    logs = _strip_ansi(buffer.getvalue())[-2000:]
    _show_with_details(
        "Restore complete",
        _format_restore_summary(guild_id, gif_status, folders, duration, logs),
        logs,
    )


def _show_tokens_list() -> None:
    try:
        tokens = _discover_tokens_with_loading()
    except Exception as exc:
        _show_error("Token discovery failed", exc)
        return
    if not tokens:
        message_dialog(title="Tokens", text="No tokens discovered.").run()
        return
    lines = [f"{tok.user_tag} ({tok.user_id}) - {tok.source}" for tok in tokens]
    message_dialog(title="Discovered Tokens", text="\n".join(lines)).run()


def _handle_startup_add() -> None:
    selection = _discover_and_choose_token("Select account for auto-backup")
    if not selection:
        return
    root = _project_root()
    executable = Path(sys.executable if getattr(sys, "frozen", False) else root / "main.py")
    if executable.suffix == ".py":
        command = f'cmd.exe /C python "{executable}" auto-backup --account-id {selection.user_id} --working-dir "{root}"'
    else:
        command = f'"{executable}" auto-backup --account-id {selection.user_id} --working-dir "{root}"'
    script_path = write_startup_script(command, root)
    message_dialog(title="Startup", text=f"Auto-backup script saved to {script_path}").run()


def _handle_startup_remove() -> None:
    if remove_startup_script():
        message_dialog(title="Startup", text="Startup script removed.").run()
    else:
        message_dialog(title="Startup", text="No startup script found.").run()


def run_tui() -> None:
    while True:
        action = radiolist_dialog(
            title="Discord Backup",
            text="Select an action (use arrow keys, Enter to confirm)",
            values=[
                ("backup_scan", "Backup (scan tokens on this machine)"),
                ("backup_manual", "Backup (paste token manually)"),
                ("restore", "Restore from an existing .bkup file"),
                ("tokens", "View discovered tokens"),
                ("startup_add", "Add auto-backup to Windows startup"),
                ("startup_remove", "Remove auto-backup from Windows startup"),
                ("exit", "Exit"),
            ],
            ok_text="Select",
            cancel_text="Quit",
        ).run()

        if action in (None, "exit"):
            message_dialog(title="Goodbye", text="Stay safe and keep your backups handy!").run()
            break
        if action == "backup_scan":
            selection = _discover_and_choose_token("Select token for backup")
            if selection:
                _run_backup_flow(selection.token)
        elif action == "backup_manual":
            token = _prompt_for_token("Manual Backup")
            if token:
                _run_backup_flow(token)
        elif action == "restore":
            _run_restore_flow()
        elif action == "tokens":
            _show_tokens_list()
        elif action == "startup_add":
            _handle_startup_add()
        elif action == "startup_remove":
            _handle_startup_remove()


__all__ = ["run_tui"]
