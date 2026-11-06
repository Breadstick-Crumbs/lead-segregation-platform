"""Speech-to-text for WhatsApp voice notes."""

from typing import Protocol

from openai import APIConnectionError, APIStatusError, AsyncOpenAI, RateLimitError

from app.core.config import Settings

# WhatsApp voice notes are OGG/Opus; the transcription API infers the format from the name.
_EXTENSIONS = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/amr": "amr",
    "audio/wav": "wav",
}


class TranscriptionError(Exception):
    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class Transcriber(Protocol):
    async def transcribe(self, audio: bytes, *, mime_type: str) -> str: ...


def audio_filename(mime_type: str) -> str:
    base = mime_type.split(";", maxsplit=1)[0].strip().lower()
    return f"voice-note.{_EXTENSIONS.get(base, 'ogg')}"


class OpenAITranscriber:
    def __init__(self, settings: Settings) -> None:
        if settings.openai_api_key is None:
            raise TranscriptionError("OPENAI_API_KEY is not configured", retryable=False)
        self._client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        self._model = settings.openai_transcription_model

    async def transcribe(self, audio: bytes, *, mime_type: str) -> str:
        try:
            result = await self._client.audio.transcriptions.create(
                model=self._model, file=(audio_filename(mime_type), audio, mime_type)
            )
        except (RateLimitError, APIConnectionError) as exc:
            raise TranscriptionError(str(exc), retryable=True) from exc
        except APIStatusError as exc:
            raise TranscriptionError(str(exc), retryable=exc.response.is_server_error) from exc
        return result.text.strip()
