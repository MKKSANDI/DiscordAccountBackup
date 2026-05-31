from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import typer
from colorama import init as colorama_init

from .backup import BackupError, BackupService
from .config import AppConfig
from .console import Console
from .http_client import DiscordHTTPClient
from .restore import RestoreError, RestoreService
from .results import BackupResult
from .startup import remove_startup_script, write_startup_script
from .token_discovery import DiscoveredToken, discover_tokens

app = typer.Typer(add_completion=False, help="Modern Discord backup & restore toolkit.")
colorama_init(autoreset=True)


def _load_config_and_console() -> tuple[AppConfig, Console]:
    config = AppConfig.load()
    console = Console(accent=config.colour)
    return config, console


async def _select_token(http: DiscordHTTPClient, console: Console) -> Optional[DiscoveredToken]:
    tokens = await discover_tokens(http)
    if not tokens:
        console.warn("No tokens discovered on this system.")
        return None
    console.info("Discovered tokens:")
    for idx, info in enumerate(tokens):
        console.info(f"[{idx}] {info.user_tag} ({info.source})", indent=2)
    choice = typer.prompt("Select token index", default="0")
    try:
        selected = tokens[int(choice)]
        console.success(f"Selected {selected.user_tag}")
        return selected
    except (ValueError, IndexError):
        console.error("Invalid selection")
        return None


async def _run_backup(token: str | None, config: AppConfig, console: Console, scan: bool) -> BackupResult | None:
    http = await DiscordHTTPClient.create(resolve_build_number=False)
    try:
        token_value = token
        if scan or not token_value:
            selection = await _select_token(http, console)
            if not selection:
                return None
            token_value = selection.token
        if not token_value:
            console.error("Token is required to run a backup.")
            return None

        service = BackupService(http=http, token=token_value, config=config, console=console)
        result = await service.run()
        path = await service.save(result)
        console.info(f"Backup stored at {path}")
        summary = result.summary()
        console.info(
            "Guilds: {success}/{total}".format(**summary["guilds"]),
            indent=2,
        )
        console.info(
            "Group chats: {success}/{total}".format(**summary["group_chats"]),
            indent=2,
        )
        console.info(f"Duration: {result.duration:.1f}s", indent=2)
        return result
    finally:
        await http.aclose()


@app.command()
def backup(
    token: str | None = typer.Option(None, help="Discord user token"),
    scan: bool = typer.Option(False, "--scan", help="Auto-discover tokens on this machine"),
) -> None:
    """Create a new backup interactively."""
    config, console = _load_config_and_console()
    try:
        asyncio.run(_run_backup(token, config, console, scan))
    except BackupError as exc:
        console.error(str(exc))
        raise typer.Exit(code=1)


async def _run_restore(
    backup_path: Path,
    token: str,
    bot_token: str,
    restore_folders: bool,
    allow_version_mismatch: bool,
    console: Console,
) -> None:
    if not backup_path.exists():
        console.error(f"Backup file not found: {backup_path}")
        return
    data = json.loads(backup_path.read_text(encoding="utf-8"))
    http = await DiscordHTTPClient.create(resolve_build_number=False)
    try:
        service = RestoreService(http=http, token=token, bot_token=bot_token, backup=data, console=console)
        summary = await service.run(
            restore_folders=restore_folders,
            expected_version=data.get("version"),
            allow_mismatch=allow_version_mismatch,
        )
        console.success("Restore complete")
        console.info(f"Backup guild id: {summary.guild_id}")
        console.info(f"Favourite GIFs: {summary.favourite_gifs_status}")
        console.info(
            "Folders restored" if summary.folder_restore_attempted else "Folders skipped",
            indent=2,
        )
        console.info(f"Duration: {summary.duration:.1f}s", indent=2)
    finally:
        await http.aclose()


@app.command()
def restore(
    backup_file: Path = typer.Argument(..., exists=True, readable=True, help="Path to .bkup file"),
    token: str = typer.Option(..., prompt=True, hide_input=True, help="Discord user token"),
    bot_token: str = typer.Option(..., prompt=True, hide_input=True, help="Bot token for user lookups"),
    restore_folders: bool = typer.Option(True, help="Restore guild folders"),
    allow_version_mismatch: bool = typer.Option(False, help="Ignore backup version mismatches"),
) -> None:
    """Restore from a backup file."""
    _, console = _load_config_and_console()
    try:
        asyncio.run(
            _run_restore(
                backup_file,
                token,
                bot_token,
                restore_folders,
                allow_version_mismatch,
                console,
            )
        )
    except RestoreError as exc:
        console.error(str(exc))
        raise typer.Exit(code=1)


@app.command()
def auto_backup(
    account_id: str = typer.Option(..., help="Discord account ID to auto-backup"),
    working_dir: Path = typer.Option(Path.cwd(), help="Working directory containing config.yml"),
) -> None:
    """Internal helper used by the startup script."""
    config, console = _load_config_and_console()
    working_dir = working_dir.resolve()
    if working_dir.exists():
        import os

        os.chdir(working_dir)
    else:
        console.warn(f"Working directory not found: {working_dir}")
    async def runner() -> None:
        http = await DiscordHTTPClient.create(resolve_build_number=False)
        try:
            tokens = await discover_tokens(http)
            token_value = None
            for item in tokens:
                if item.user_id == account_id:
                    token_value = item.token
                    console.success(f"Found token for {item.user_tag}")
                    break
            if not token_value:
                console.error("No matching token found for auto-backup")
                return
            service = BackupService(http=http, token=token_value, config=config, console=console)
            result = await service.run()
            await service.save(result)
            console.success("Auto-backup finished")
        finally:
            await http.aclose()
    asyncio.run(runner())


@app.command()
def startup_add(
    scan: bool = typer.Option(True, help="Scan for tokens to choose from"),
) -> None:
    """Add the current executable/script to Windows startup for auto-backup."""
    config, console = _load_config_and_console()

    async def runner() -> None:
        http = await DiscordHTTPClient.create(resolve_build_number=False)
        try:
            selection: DiscoveredToken | None = None
            if scan:
                selection = await _select_token(http, console)
            if not selection:
                token_input = typer.prompt("Enter user token for auto-backup", hide_input=True)
                if not token_input:
                    console.error("Token required for auto-backup")
                    return
                selection = DiscoveredToken(token=token_input, user_tag="manual", user_id=typer.prompt("Enter account id"), source="manual")

            executable = Path(sys.executable if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent / "main.py")
            if executable.suffix == ".py":
                command = f'cmd.exe /C python "{executable}" auto-backup --account-id {selection.user_id}'
            else:
                command = f'"{executable}" auto-backup --account-id {selection.user_id}'
            script_path = write_startup_script(command, Path.cwd())
            console.success(f"Startup script written to {script_path}")
        finally:
            await http.aclose()

    asyncio.run(runner())


@app.command()
def startup_remove() -> None:
    """Remove the auto-backup startup script if present."""
    if remove_startup_script():
        typer.echo("Startup script removed")
    else:
        typer.echo("No startup script found")


@app.command()
def tokens() -> None:
    """List tokens discovered on this machine."""
    _, console = _load_config_and_console()

    async def runner() -> None:
        http = await DiscordHTTPClient.create(resolve_build_number=False)
        try:
            tokens = await discover_tokens(http)
            if not tokens:
                console.warn("No tokens discovered")
                return
            for item in tokens:
                console.info(f"{item.user_tag} ({item.user_id}) - {item.source}")
        finally:
            await http.aclose()

    asyncio.run(runner())


def run() -> None:
    app()


__all__ = ["app", "run"]
