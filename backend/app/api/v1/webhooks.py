"""Public webhook endpoints. Authenticated by signature, not by user login."""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from app.api.deps import SessionDep, SettingsDep
from app.core.errors import AppError, AuthenticationError, PermissionDeniedError
from app.core.logging import get_logger
from app.integrations.whatsapp import verify_signature
from app.services.whatsapp_inbox import WhatsAppInbox

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = get_logger(__name__)

# Meta's webhook payloads are at most 3 MB. The endpoint is public, so anything larger is
# refused before it is buffered (the signature can only be checked on the full body).
MAX_WEBHOOK_BYTES = 3 * 1024 * 1024


class WebhookNotConfiguredError(AppError):
    status_code = 503
    code = "webhook_not_configured"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


async def _read_capped(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_WEBHOOK_BYTES:
        raise PayloadTooLargeError("Webhook payload too large")
    body = bytearray()
    async for chunk in request.stream():  # also covers chunked uploads without a length
        body.extend(chunk)
        if len(body) > MAX_WEBHOOK_BYTES:
            raise PayloadTooLargeError("Webhook payload too large")
    return bytes(body)


@router.get("/whatsapp", response_class=PlainTextResponse)
async def verify_whatsapp_webhook(
    settings: SettingsDep,
    mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> str:
    """Meta's one-time subscription check: echo the challenge if the verify token matches."""
    expected = settings.whatsapp_verify_token
    if expected is None:
        raise WebhookNotConfiguredError("WHATSAPP_VERIFY_TOKEN is not configured")
    if mode != "subscribe" or token != expected.get_secret_value() or challenge is None:
        raise PermissionDeniedError("Webhook verification failed")
    return challenge


@router.post("/whatsapp", status_code=status.HTTP_200_OK)
async def receive_whatsapp_webhook(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    signature: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
) -> Response:
    """Verify, store, enqueue, return 200. All parsing happens in the worker, so Meta gets a
    fast answer and a parsing bug can't make it retry (or drop) the delivery."""
    secret = settings.whatsapp_app_secret
    if secret is None:
        # Fail closed: without the secret anyone could post fake leads.
        raise WebhookNotConfiguredError("WHATSAPP_APP_SECRET is not configured")
    body = await _read_capped(request)
    if not verify_signature(body, signature, secret.get_secret_value()):
        logger.warning("whatsapp_webhook_bad_signature")
        raise AuthenticationError("Invalid webhook signature")
    try:
        payload: Any = json.loads(body)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        logger.warning("whatsapp_webhook_unusable_body")
        return Response(status_code=status.HTTP_200_OK)  # signed but unusable: don't retry

    await WhatsAppInbox(session, settings).accept(payload)
    return Response(status_code=status.HTTP_200_OK)
