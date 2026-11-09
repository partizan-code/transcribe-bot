import asyncio
import importlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from assemblyai import TranscriptStatus

import transcribe
from errors import AppError
from tests.helpers import settings
from transcribe import TranscriptionService


class TranscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = TemporaryDirectory(prefix="transcribe-sdk-test-")
        self.path = Path(self.directory.name) / "input.wav"
        self.path.write_bytes(b"synthetic-test-data")

    async def asyncTearDown(self) -> None:
        self.directory.cleanup()

    def sdk(self, **overrides: object) -> SimpleNamespace:
        result = SimpleNamespace(
            **{
                "status": TranscriptStatus.completed,
                "text": "hello",
                "utterances": None,
                "words": None,
                "language_code": "en",
                **overrides,
            }
        )
        pending = SimpleNamespace(wait_for_completion=AsyncMock(return_value=result))
        return SimpleNamespace(submit=AsyncMock(return_value=pending), aclose=AsyncMock())

    async def test_success_and_options(self) -> None:
        sdk = self.sdk()
        service = TranscriptionService(settings(), sdk)
        result = await service.transcribe(self.path)
        self.assertEqual(result.text, "hello")
        self.assertEqual(result.language, "en")
        sdk.submit.assert_awaited_once()
        self.assertTrue(sdk.submit.call_args.args[0].closed)
        options = sdk.submit.call_args.kwargs["config"]
        self.assertTrue(options.language_detection)
        self.assertTrue(options.speaker_labels)
        await service.aclose()
        sdk.aclose.assert_awaited_once()

    async def test_empty_and_missing_speaker(self) -> None:
        word = SimpleNamespace(text="word", start=0, end=100)
        utterance = SimpleNamespace(text="word", start=0, end=100, speaker=None, words=[word])
        result = await TranscriptionService(
            settings(), self.sdk(text=None, utterances=[utterance], words=[word])
        ).transcribe(self.path)
        self.assertEqual(result.utterances[0].words[0].end, 100)
        self.assertIsNone(result.utterances[0].speaker)

    async def test_service_error_does_not_leak(self) -> None:
        for status in ("remote", "network", "timeout"):
            with self.subTest(status=status):
                sdk = self.sdk(status=TranscriptStatus.error)
                if status == "network":
                    sdk.submit.side_effect = RuntimeError("private-secret-url")
                if status == "timeout":
                    sdk.submit.side_effect = TimeoutError("private-secret-url")
                with self.assertRaises(AppError) as caught:
                    await TranscriptionService(settings(), sdk).transcribe(self.path)
                self.assertNotIn("private-secret-url", str(caught.exception))
                sdk.submit.assert_awaited_once()

    async def test_total_timeout_and_cancellation_do_not_resubmit(self) -> None:
        sdk = self.sdk()

        async def hang() -> None:
            await asyncio.Event().wait()

        sdk.submit.return_value.wait_for_completion.side_effect = hang
        with self.assertRaises(AppError) as caught:
            await TranscriptionService(settings(transcription_timeout=0.02), sdk).transcribe(
                self.path
            )
        self.assertEqual(caught.exception.code, "transcription_timeout")
        sdk.submit.assert_awaited_once()
        task = asyncio.create_task(TranscriptionService(settings(), sdk).transcribe(self.path))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(sdk.submit.await_count, 2)

    async def test_cancel_during_upload_closes_file_before_returning(self) -> None:
        sdk = self.sdk()
        entered = asyncio.Event()

        async def upload(audio: object, **kwargs: object) -> None:
            entered.set()
            await asyncio.Event().wait()

        sdk.submit.side_effect = upload
        task = asyncio.create_task(TranscriptionService(settings(), sdk).transcribe(self.path))
        await entered.wait()
        audio = sdk.submit.call_args.args[0]
        self.assertFalse(audio.closed)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(audio.closed)
        self.path.unlink()

    async def test_import_does_not_construct_sdk(self) -> None:
        with patch("assemblyai.prerecorded.v2.AsyncTranscriber") as sdk:
            importlib.reload(transcribe)
            sdk.assert_not_called()
        importlib.reload(transcribe)

    async def test_real_sdk_can_be_created_and_closed_without_requests(self) -> None:
        service = TranscriptionService(settings())
        await service.aclose()
        self.assertTrue(service._client.http_client.is_closed)
