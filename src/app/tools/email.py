import base64
import re
from dataclasses import dataclass
from html import escape

import httpx

RESEND_URL = "https://api.resend.com/emails"
MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
BARE_URL = re.compile(r'(?<!href=")(?<!">)(https?://[^\s<]+)')
BOLD = re.compile(r"\*\*(.+?)\*\*")
BULLET = re.compile(r"^\s*[-*]\s+")

def _inline(line: str) -> str:
    html = escape(line, quote=False)
    html = MD_LINK.sub(lambda m: f'<a href="{escape(m.group(2))}">{m.group(1)}</a>', html)
    html = BARE_URL.sub(lambda m: f'<a href="{m.group(1)}">{m.group(1)}</a>', html)
    return BOLD.sub(r"<strong>\1</strong>", html)

@dataclass(frozen=True)
class Inline:
    """
    An image shown inside the email, referenced from the HTML as cid:<content_id>.
    """

    content_id: str
    filename: str
    data: bytes
    alt: str

def _img(image: Inline) -> str:
    return (
        f'<p><img src="cid:{escape(image.content_id)}" alt="{escape(image.alt)}" '
        'style="max-width: 100%; height: auto; border-radius: 8px; display: block"></p>'
    )

def markdown_to_html(body: str, images: list[Inline] | None = None) -> str:
    """
    Simple markdown (paragraphs, bullets, **bold**, links) to email-safe HTML. The first
    image goes above the text as a header, the others below it.
    """
    parts = []
    for block in re.split(r"\n\s*\n", body.strip()):
        lines = block.splitlines()
        if lines and all(BULLET.match(line) for line in lines):
            items = "".join(f"<li>{_inline(BULLET.sub('', line))}</li>" for line in lines)
            parts.append(f"<ul>{items}</ul>")
        else:
            parts.append(f"<p>{'<br>'.join(_inline(line) for line in lines)}</p>")
    images = images or []
    parts = [_img(image) for image in images[:1]] + parts + [_img(image) for image in images[1:]]
    content = "\n".join(parts)
    return (
        '<div style="font-family: -apple-system, Segoe UI, sans-serif; font-size: 15px; '
        f'line-height: 1.55; color: #16201c; max-width: 600px">{content}</div>'
    )

class ResendError(Exception):
    pass

class Resend:
    def __init__(self, api_key: str, sender: str, http: httpx.AsyncClient | None = None) -> None:
        self._api_key = api_key
        self.sender = sender
        self._http = http or httpx.AsyncClient(timeout=20)

    async def send(
        self, to: str, subject: str, body: str, images: list[Inline] | None = None
    ) -> str:
        """
        Sends one email and returns Resend's id. Addresses are lowercased: providers treat them
        as case-insensitive, and Resend's test sender only matches the account email exactly.
        Images go inline as attachments with a content id.
        """
        to = to.strip().lower()
        payload = {
            "from": self.sender,
            "to": [to],
            "subject": subject,
            "text": body,
            "html": markdown_to_html(body, images),
        }
        if images:
            payload["attachments"] = [
                {
                    "filename": image.filename,
                    "content": base64.b64encode(image.data).decode(),
                    "content_id": image.content_id,
                }
                for image in images
            ]
        response = await self._http.post(
            RESEND_URL, headers={"Authorization": f"Bearer {self._api_key}"}, json=payload
        )
        payload = response.json() if response.content else {}
        if response.status_code >= 400:
            raise ResendError(payload.get("message") or f"Resend answered {response.status_code}")
        return payload["id"]
