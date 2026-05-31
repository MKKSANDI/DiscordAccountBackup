# DiscordAccountBackup

`DiscordAccountBackup` is a Windows-first Python CLI/TUI project with dependency bootstrapping built in.

## Requirements

- Python 3.10+
- Windows PowerShell / Command Prompt

## Quick Start (Windows)

```bat
run.bat
```

`run.bat` will:
1. Request administrator privileges.
2. Create `.venv` automatically if it does not exist.
3. Install dependencies from `requirements.txt`.
4. Launch `main.py`.
5. Keep the window open if launch fails, so errors are visible.

## Run From Source

Direct runs also perform dependency checks and auto-install missing packages when possible:

```powershell
python main.py --help
python main.py
```

## CLI Usage

```powershell
python main.py backup --help
python main.py restore --help
python main.py tokens
python main.py startup-add --help
python main.py startup-remove
```

## Troubleshooting

- Launcher diagnostics: `%TEMP%\DiscordAccountBackup\launcher.log`
- Crash diagnostics: `%TEMP%\DiscordAccountBackup\crash.log`
- If TUI cannot open file-picker dialogs, restore mode falls back to manual path entry.

## Project Layout

- `main.py`: entrypoint, runtime dependency bootstrap, CLI/TUI dispatch
- `run.bat`: elevated Windows launcher
- `config.yml`: runtime settings
- `requirements.txt`: Python dependencies
- `discord_backup/`: app modules (CLI, TUI, HTTP client, config, models, startup helpers)
