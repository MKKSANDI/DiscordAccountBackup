# Discord Backup Source (Reconstructed)

This repository contains a reconstructed Python source tree derived from a bundled `DiscordBackup.exe` build.
It is organized as a normal project so missing behavior can be finished incrementally.

## Current status

- package layout is restored
- CLI/TUI entry flow is wired
- configuration and model layers are present
- some service-heavy internals still need deeper parity work

## Project layout

- `main.py`: entry point
- `config.yml`: runtime config
- `discord_backup/` package:
  - `cli.py`: command routing
  - `tui.py`: interactive terminal frontend
  - `backup.py` / `restore.py`: backup and restore paths (partially reconstructed)
  - `token_discovery.py`: token discovery logic (partially reconstructed)
  - `config.py`, `models.py`, `http_client.py`, `identity.py`, `results.py`, `utils.py`

## Run

```powershell
cd "D:\Moved From C\Desktop\Apps\discord backup\Unpack\discord_backup_source"
python -m pip install -r requirements.txt
python main.py --help
python main.py
```

## Known gaps

- full backup/restore parity requires additional implementation and validation
- some runtime flows still use placeholder behavior

## Repository goal

Keep a clean, source-first baseline that can be audited, tested, and completed without binary-only tooling.
