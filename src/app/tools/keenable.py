import httpx

from contract import SearchResult

SEARCH_URL = "https://api.keenable.ai/v1/search"
SNIPPET_LENGTH = 300
OUT_STATUSES = {401, 402, 403}

class KeenableError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"Keenable answered {status}: {message}")
        self.status = status

    @property
    def out(self) -> bool:
        """
        True when Keenable won't answer again this run: bad key, no credits or blocked.
        """
        return self.status in OUT_STATUSES

class Keenable:
    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self._http = http or httpx.AsyncClient(timeout=15)

    async def search(self, query: str, k: int = 5) -> list[SearchResult]:
        response = await self._http.post(
            SEARCH_URL,
            headers={"X-API-Key": self._api_key},
            json={"query": query, "max_results": k, "snippet_max_length": SNIPPET_LENGTH},
        )
        if response.status_code >= 400:
            raise KeenableError(response.status_code, response.text[:200])
        return [
            SearchResult(
                title=" ".join((item.get("title") or "").split()),
                url=item["url"],
                snippet=" ".join((item.get("snippet") or item.get("description") or "").split()),
            )
            for item in response.json().get("results", [])[:k]
            if item.get("url")
        ]
