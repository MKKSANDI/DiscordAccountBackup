from __future__ import annotations

import asyncio
import json
from collections.abc import Iterable
from typing import Any

import httpx

from .identity import DiscordBrowserIdentity

API_BASE = "https://discord.com/api/v9"


class DiscordHTTPClient:
    """Async HTTP helper with Discord flavoured defaults and rate-limit handling."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        identity: DiscordBrowserIdentity,
        *,
        concurrency: int = 6,
        max_retries: int = 4,
    ) -> None:
        self._client = client
        self.identity = identity
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._max_retries = max(1, max_retries)

    @classmethod
    async def create(
        cls,
        *,
        identity: DiscordBrowserIdentity | None = None,
        timeout: float = 30.0,
        concurrency: int = 6,
        max_retries: int = 4,
        resolve_build_number: bool = True,
    ) -> "DiscordHTTPClient":
        identity = identity or DiscordBrowserIdentity()
        client = httpx.AsyncClient(base_url=API_BASE, timeout=timeout, http2=True)
        if resolve_build_number:
            await identity.ensure_build_number(client)
        return cls(client, identity, concurrency=concurrency, max_retries=max_retries)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        json_payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        super_properties: bool = True,
        include_locale: bool = True,
        include_debug: bool = False,
        expected_status: Iterable[int] | None = None,
        retries: int | None = None,
        referer: str | None = "https://discord.com/channels/@me",
        origin: str | None = "https://discord.com",
        context: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if not url.startswith("http"):
            # treat as API path
            url = url.lstrip("/")
        attempt_total = retries or self._max_retries
        last_error: Exception | None = None

        for attempt in range(1, attempt_total + 1):
            async with self._semaphore:
                request_headers = await self.identity.build_headers(
                    self._client,
                    method=method,
                    authorization=token,
                    referer=referer,
                    origin=origin,
                    super_properties=super_properties,
                    include_locale=include_locale,
                    include_debug=include_debug,
                    timezone="Europe/London",
                    context=context,
                    extra=headers,
                )
                try:
                    response = await self._client.request(
                        method.upper(),
                        url,
                        json=json_payload,
                        params=params,
                        headers=request_headers,
                    )
                except httpx.HTTPError as exc:
                    last_error = exc
                    await asyncio.sleep(1.5 * attempt)
                    continue

            if response.status_code == 429:
                delay = 1.5
                try:
                    data = response.json()
                    delay = float(data.get("retry_after", delay)) + 0.25
                except (ValueError, json.JSONDecodeError):
                    pass
                await asyncio.sleep(delay)
                continue

            if response.status_code >= 500 and attempt < attempt_total:
                await asyncio.sleep(1.5 * attempt)
                continue

            if expected_status and response.status_code not in expected_status:
                return response

            return response

        if last_error:
            raise last_error
        raise RuntimeError(f"Failed to complete {method} {url} after {attempt_total} attempts")

    async def json(
        self,
        method: str,
        url: str,
        *,
        token: str | None = None,
        expected_status: Iterable[int] | None = None,
        **kwargs: Any,
    ) -> Any:
        response = await self.request(
            method,
            url,
            token=token,
            expected_status=expected_status,
            **kwargs,
        )
        response.raise_for_status()
        if response.content:
            return response.json()
        return None


__all__ = ["DiscordHTTPClient", "API_BASE"]
