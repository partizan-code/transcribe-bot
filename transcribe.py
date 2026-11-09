"""Async AssemblyAI adapter; importing this module performs no requests."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from assemblyai import TranscriptStatus
from assemblyai.async_client import AsyncClient
from assemblyai.prerecorded.v2 import AsyncTranscriber, TranscriptionConfig
from assemblyai.types import Settings as SDKSettings

from errors import AppError


@dataclass(frozen=True)
class Word:
    text: str
    start: int | None = None
    end: int | None = None
    speaker: str | None = None


@dataclass(frozen=True)
class Utterance:
    text: str
    start: int | None = None
    end: int | None = None
    speaker: str | None = None
    words: tuple[Word, ...] = ()


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str | None = None
    utterances: tuple[Utterance, ...] = ()
    words: tuple[Word, ...] = ()


def _time(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _word(value: Any) -> Word:
    return Word(
        text=value.text or "",
        start=_time(value.start),
        end=_time(value.end),
        speaker=getattr(value, "speaker", None),
    )


def normalize(result: Any) -> Transcript:
    utterances = tuple(
        Utterance(
            text=item.text or "",
            start=_time(item.start),
            end=_time(item.end),
            speaker=item.speaker,
            words=tuple(_word(word) for word in (item.words or [])),
        )
        for item in (result.utterances or [])
    )
    return Transcript(
        text=result.text or "",
        language=getattr(result, "language_code", None),
        utterances=utterances,
        words=tuple(_word(word) for word in (result.words or [])),
    )


class TranscriptionService:
    def __init__(self, settings: ModuleType, sdk: Any = None) -> None:
        self.settings = settings
        self._client = None
        if sdk is None:
            self._client = AsyncClient(
                api_key=settings.API_KEY,
                settings=SDKSettings(http_timeout=settings.REQUEST_TIMEOUT),
            )
            sdk = AsyncTranscriber(client=self._client)
        self.sdk = sdk

    async def transcribe(self, path: Path) -> Transcript:
        # Exactly one submission; a timeout does not cancel the remote paid job.
        try:
            async with asyncio.timeout(self.settings.TRANSCRIPTION_TIMEOUT):
                # Own the handle: SDK path-based async generators can outlive a
                # cancelled upload, preventing immediate deletion on Windows.
                with path.open("rb") as audio:
                    pending = await self.sdk.submit(
                        audio,
                        config=TranscriptionConfig(language_detection=True, speaker_labels=True),
                    )
                result = await pending.wait_for_completion()
            if result.status == TranscriptStatus.error:
                raise AppError("service", "AssemblyAI не смог распознать запись.")
            return normalize(result)
        except TimeoutError:
            raise AppError(
                "transcription_timeout",
                "Время ожидания AssemblyAI истекло. Задание у провайдера может продолжать "
                "обработку; автоматической повторной отправки не будет.",
            ) from None
        except AppError:
            raise
        except Exception:
            raise AppError(
                "service",
                "Ошибка связи с AssemblyAI. Результат отправки может быть неизвестен; "
                "автоматической повторной отправки не будет.",
            ) from None

    async def aclose(self) -> None:
        try:
            await self.sdk.aclose()
        finally:
            if self._client is not None:
                await self._client.aclose()
