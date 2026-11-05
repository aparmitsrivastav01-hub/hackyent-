import asyncio
import random
from typing import Any

import httpx


class TinyFishError(RuntimeError):
    pass


class TinyFishClient:
    """Small HTTP client for TinyFish's documented Search and Fetch APIs."""

    SEARCH_URL = "https://api.search.tinyfish.ai"
    FETCH_URL = "https://api.fetch.tinyfish.ai"

    def __init__(self, api_key: str, timeout: float = 45, client: httpx.AsyncClient | None = None):
        self.api_key = api_key
        self.timeout = timeout
        self._client = client

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict:
        if not self.api_key:
            raise TinyFishError("TINYFISH_API_KEY is not configured")
        own_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout)
        try:
            for attempt in range(3):
                try:
                    response = await client.request(method, url, headers={"X-API-Key": self.api_key}, **kwargs)
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < 2:
                            await asyncio.sleep((2 ** attempt) + random.random() / 4)
                            continue
                    response.raise_for_status()
                    return response.json()
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    if attempt == 2:
                        raise TinyFishError(f"TinyFish request failed after retries: {exc}") from exc
                    await asyncio.sleep((2 ** attempt) + random.random() / 4)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 429 or exc.response.status_code >= 500:
                        if attempt < 2:
                            await asyncio.sleep((2 ** attempt) + random.random() / 4)
                            continue
                    raise TinyFishError(f"TinyFish returned HTTP {exc.response.status_code}") from exc
            raise TinyFishError("TinyFish request failed")
        finally:
            if own_client:
                await client.aclose()

    async def search(self, query: str, include_domains: list[str] | None = None, location: str = "IN") -> dict:
        params: dict[str, Any] = {"query": query, "location": location}
        if include_domains:
            params["include_domains"] = ",".join(include_domains)
        return await self._request("GET", self.SEARCH_URL, params=params)

    async def fetch(self, urls: list[str]) -> dict:
        if not urls:
            return {"results": [], "errors": []}
        return await self._request("POST", self.FETCH_URL, json={"urls": urls[:10], "format": "markdown", "links": True})

