"""
Read-only web access for the brain: DuckDuckGo search and page fetching as clean text.
Fetching refuses non-public addresses so the brain can't reach the dashboard or other
internal services through it.
"""

import asyncio
import ipaddress
import re
import socket
from collections.abc import Awaitable, Callable
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from contract import PageContent, SearchResult

SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36"
)
TEXT_LIMIT = 20_000
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
BARE_DOMAIN = re.compile(r"^(?:[a-z0-9-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]\S*)?$", re.IGNORECASE)
PDF_NOTE = (
    "This link is a PDF document, which can't be read from a link. Ask the founder to send "
    "the file in Telegram instead."
)

class _Results(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._field: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = (dict(attrs).get("class") or "").split()
        if tag == "a" and "result__a" in classes:
            self.results.append({"title": "", "url": dict(attrs).get("href") or "", "snippet": ""})
            self._field = "title"
        elif "result__snippet" in classes and self.results:
            self._field = "snippet"

    def handle_endtag(self, tag: str) -> None:
        if tag in ("a", "td", "div"):
            self._field = None

    def handle_data(self, data: str) -> None:
        if self._field and self.results:
            self.results[-1][self._field] += data

def _real_url(href: str) -> str:
    parsed = urlparse(href if "://" in href else f"https:{href}")
    target = parse_qs(parsed.query).get("uddg")
    return target[0] if target else href

def parse_results(html: str, k: int) -> list[SearchResult]:
    parser = _Results()
    parser.feed(html)
    results = []
    for item in parser.results:
        url = _real_url(item["url"])
        if not url.startswith("http") or "duckduckgo.com/y.js" in url:
            continue
        results.append(
            SearchResult(
                title=" ".join(item["title"].split()),
                url=url,
                snippet=" ".join(item["snippet"].split()),
            )
        )
        if len(results) == k:
            break
    return results

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "template", "iframe", "nav", "footer", "form"}
    BLOCK = {
        "p", "div", "br", "li", "ul", "ol", "section", "article", "main", "header", "tr",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

def html_to_text(html: str) -> tuple[str | None, str]:
    parser = _Text()
    parser.feed(html)
    lines = (" ".join(line.split()) for line in "".join(parser.parts).splitlines())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    title = " ".join(parser.title.split()) or None
    return title, text

def full_url(url: str) -> str:
    """
    A link as people type it ("mysite.com/pricing") with https:// added; anything else as is.
    """
    url = url.strip()
    if "://" not in url and BARE_DOMAIN.match(url):
        return f"https://{url}"
    return url

async def is_public(url: str) -> bool:
    parsed = urlparse(full_url(url))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError:
        return False
    addresses = {info[4][0] for info in infos}
    return bool(addresses) and all(ipaddress.ip_address(a).is_global for a in addresses)

def _is_pdf(content_type: str, url: str) -> bool:
    if "pdf" in content_type:
        return True
    return "html" not in content_type and urlparse(url).path.lower().endswith(".pdf")

class Web:
    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        check_address: Callable[[str], Awaitable[bool]] = is_public,
    ) -> None:
        self._http = http or httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT})
        self._check_address = check_address

    async def search(self, query: str, k: int = 5) -> list[SearchResult]:
        response = await self._http.post(SEARCH_URL, data={"q": query})
        response.raise_for_status()
        return parse_results(response.text, k)

    async def fetch(self, url: str) -> PageContent | None:
        """
        The page as clean text, or None when it can't be opened. A PDF gives a short note
        saying why it wasn't read, so the team can ask for the file instead.
        """
        current = full_url(url)
        for _ in range(MAX_REDIRECTS + 1):
            if not await self._check_address(current):
                return None
            async with self._http.stream("GET", current, follow_redirects=False) as response:
                if response.is_redirect and "location" in response.headers:
                    current = urljoin(current, response.headers["location"])
                    continue
                if response.status_code >= 400:
                    return None
                kind = response.headers.get("content-type", "")
                if _is_pdf(kind, current):
                    return PageContent(url=current, title="PDF document", text=PDF_NOTE)
                if "html" not in kind and "text" not in kind:
                    return None
                body = b""
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                html = body.decode(response.encoding or "utf-8", errors="replace")
            if "html" in kind:
                title, text = html_to_text(html)
            else:
                title, text = None, html.strip()
            return PageContent(url=current, title=title, text=text[:TEXT_LIMIT])
        return None
