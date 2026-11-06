"""Meta WhatsApp Cloud API: webhook signature check, payload parsing and media download."""

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.core.text import clean
from app.models.enums import Channel, MessageKind
from app.schemas.inbound import InboundMessage

logger = get_logger(__name__)

GRAPH_HOST = "https://graph.facebook.com"
MAX_MEDIA_BYTES = 16 * 1024 * 1024  # Meta's audio limit
HTTP_TIMEOUT_SECONDS = 20.0

# Message types we turn into leads, and the kind we store them as.
_KINDS = {
    "text": MessageKind.TEXT,
    "audio": MessageKind.VOICE,
    "image": MessageKind.IMAGE,
    "video": MessageKind.VIDEO,
    "document": MessageKind.DOCUMENT,
    "button": MessageKind.INTERACTIVE,
    "interactive": MessageKind.INTERACTIVE,
}


class WhatsAppError(Exception):
    """A call to the Graph API failed. `retryable` is False when retrying can't help."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def verify_signature(raw_body: bytes, header: str | None, app_secret: str) -> bool:
    """Check Meta's `X-Hub-Signature-256: sha256=<hex>` HMAC over the exact request bytes."""
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    # Compare bytes: compare_digest raises on non-ASCII str, which would turn junk into a 500.
    received = header.removeprefix("sha256=").encode("latin-1", errors="replace")
    return hmac.compare_digest(expected.encode(), received)


def parse_webhook(payload: dict[str, Any], *, phone_number_id: str | None) -> list[InboundMessage]:
    """Extract inbound customer messages. Delivery statuses, reactions and system messages are
    ignored here (statuses matter once we send replies)."""
    messages: list[InboundMessage] = []
    for value in _message_values(payload, phone_number_id):
        names: dict[str, str | None] = {
            str(c.get("wa_id")): (c.get("profile") or {}).get("name")
            for c in _dicts(value.get("contacts"))
        }
        for raw in _dicts(value.get("messages")):
            try:
                parsed = _parse_message(raw, names)
            except (TypeError, ValueError, KeyError, OverflowError):
                # One malformed message must not drop the others in the same delivery.
                logger.warning("whatsapp_message_unparseable", wamid=str(raw.get("id")))
                continue
            if parsed is not None:
                messages.append(parsed)
    return messages


def _dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _message_values(payload: dict[str, Any], phone_number_id: str | None) -> list[dict[str, Any]]:
    values = []
    for entry in _dicts(payload.get("entry")):
        for change in _dicts(entry.get("changes")):
            value = change.get("value")
            if change.get("field") != "messages" or not isinstance(value, dict):
                continue
            our_number = (value.get("metadata") or {}).get("phone_number_id")
            if phone_number_id and our_number != phone_number_id:
                continue  # another number on the same Meta app
            values.append(value)
    return values


def _parse_message(raw: dict[str, Any], names: dict[str, str | None]) -> InboundMessage | None:
    kind = _KINDS.get(str(raw.get("type")))
    sender, wamid, timestamp = raw.get("from"), raw.get("id"), raw.get("timestamp")
    if kind is None or not sender or not wamid or not timestamp:
        logger.info("whatsapp_message_skipped", type=raw.get("type"))
        return None

    body, media_id, mime = _content(raw, kind)
    context_id = (raw.get("context") or {}).get("id")
    return InboundMessage(
        channel=Channel.WHATSAPP,
        external_id=str(wamid),
        sender_address=f"+{sender}",  # wa_id is the E.164 number without the plus
        sender_name=clean(names.get(sender), 200),
        occurred_at=datetime.fromtimestamp(int(timestamp), tz=UTC),
        body_text=body,
        kind=kind,
        media_id=media_id,
        media_mime_type=mime,
        thread_refs=(str(context_id),) if context_id else (),
    )


def _content(raw: dict[str, Any], kind: MessageKind) -> tuple[str, str | None, str | None]:
    """(body text, media id, mime type) for each supported message type."""
    message_type = str(raw["type"])
    if kind == MessageKind.TEXT:
        return str((raw.get("text") or {}).get("body", "")), None, None
    if message_type == "button":
        return str((raw.get("button") or {}).get("text", "")), None, None
    if message_type == "interactive":
        interactive = raw.get("interactive") or {}
        reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
        return str(reply.get("title", "")), None, None
    media = raw.get(message_type) or {}
    # Voice notes get their text from transcription; other media keep their caption.
    caption = "" if kind == MessageKind.VOICE else str(media.get("caption", ""))
    return caption, media.get("id"), media.get("mime_type")


class WhatsAppClient:
    def __init__(
        self,
        *,
        access_token: str,
        graph_version: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = access_token
        self._version = graph_version
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Settings) -> "WhatsAppClient":
        if settings.whatsapp_access_token is None:
            raise WhatsAppError("WHATSAPP_ACCESS_TOKEN is not configured", retryable=False)
        return cls(
            access_token=settings.whatsapp_access_token.get_secret_value(),
            graph_version=settings.whatsapp_graph_version,
        )

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """Return (bytes, mime type). The media URL lives for minutes, so fetch it right away.
        Every failure becomes a WhatsAppError that says whether retrying can help."""
        try:
            async with self._http() as http:
                meta = await http.get(f"{GRAPH_HOST}/{self._version}/{media_id}")
                if meta.status_code != httpx.codes.OK:
                    # 4xx other than 429: the media id is gone (older than 7 days) or invalid.
                    retryable = (
                        meta.is_server_error or meta.status_code == httpx.codes.TOO_MANY_REQUESTS
                    )
                    raise WhatsAppError(
                        f"Media lookup failed: {meta.status_code}", retryable=retryable
                    )
                info = meta.json()
                url = info.get("url") if isinstance(info, dict) else None
                if not isinstance(url, str):
                    raise WhatsAppError("Media lookup returned no URL", retryable=False)
                size = int(info.get("file_size") or 0)
                if size > MAX_MEDIA_BYTES:
                    raise WhatsAppError(f"Media too large: {size} bytes", retryable=False)
                download = await http.get(url)
        except httpx.HTTPError as exc:  # timeouts, connection resets, DNS
            raise WhatsAppError(f"Media request failed: {exc!r}") from exc
        except ValueError as exc:  # not JSON, or a non-numeric file_size
            raise WhatsAppError("Media lookup returned malformed data", retryable=False) from exc
        if download.status_code != httpx.codes.OK:
            raise WhatsAppError(f"Media download failed: {download.status_code}")
        if len(download.content) > MAX_MEDIA_BYTES:
            raise WhatsAppError("Media too large", retryable=False)
        return download.content, str(info.get("mime_type") or "application/octet-stream")

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=HTTP_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {self._token}"},
        )
