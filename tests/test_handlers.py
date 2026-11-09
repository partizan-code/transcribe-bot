import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from errors import AppError
from handlers import attachment_from_message, create_router, receive_media
from processing import JobManager
from tests.helpers import bot, media, message, settings


class HandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.settings = settings()
        self.manager = JobManager(
            bot(), self.settings, SimpleNamespace(transcribe=AsyncMock()), media()
        )

    async def test_voice_audio_video_documents(self) -> None:
        for kind, metadata in (
            ("voice", {}),
            ("audio", {}),
            ("video", {}),
            ("document", {"file_name": "../../unsafe.mp3"}),
            ("document", {"file_name": "movie.MP4", "mime_type": "video/mp4"}),
            ("document", {"file_name": None, "mime_type": "audio/ogg"}),
        ):
            with self.subTest(kind=kind, metadata=metadata):
                msg = message(kind, **metadata)
                await receive_media(msg, self.settings, self.manager)
                job = self.manager.queue.get_nowait()
                self.assertEqual(job.attachment.kind, kind)
                self.assertEqual(job.attachment.file_id, "test-file-id")
                self.manager.queue.task_done()
                self.manager.release(1)

    async def test_access_denied_before_metadata_download_and_queue(self) -> None:
        for change in ("user", "group", "missing", "bot"):
            msg = message(user=999 if change == "user" else 1)
            if change == "group":
                msg.chat.type = "group"
            elif change == "missing":
                msg.from_user = None
            elif change == "bot":
                msg.from_user.is_bot = True
            await receive_media(msg, self.settings, self.manager)
            self.assertTrue(self.manager.queue.empty())
            self.manager.bot.get_file.assert_not_called()

    async def test_oversized_and_too_long_media(self) -> None:
        for metadata in ({"file_size": 20_000_001}, {"file_size": 0}, {"duration": 3601}):
            with self.subTest(metadata=metadata):
                await receive_media(message(**metadata), self.settings, self.manager)
                self.assertTrue(self.manager.queue.empty())
                self.assertFalse(self.manager.active_users)

    async def test_missing_metadata_is_checked_later(self) -> None:
        await receive_media(message(file_size=None, duration=None), self.settings, self.manager)
        self.assertEqual(self.manager.queue.qsize(), 1)

    async def test_status_failure_or_cancel_releases_reservation(self) -> None:
        for error in (RuntimeError("unavailable"), asyncio.CancelledError()):
            msg = message()
            msg.answer.side_effect = error
            with self.assertRaises(type(error)):
                await receive_media(msg, self.settings, self.manager)
            self.assertFalse(self.manager.active_users)
            self.assertFalse(self.manager.reservations)

    async def test_duplicate_and_full_queue_do_not_download(self) -> None:
        manager = JobManager(bot(), settings(queue_size=1), SimpleNamespace(), media())
        await receive_media(message(), manager.settings, manager)
        duplicate, full = message(), message(user=2)
        await receive_media(duplicate, manager.settings, manager)
        await receive_media(full, manager.settings, manager)
        self.assertIn("предыдущая", duplicate.answer.call_args.args[0])
        self.assertIn("заполнена", full.answer.call_args.args[0])
        self.assertEqual(manager.queue.qsize(), 1)
        manager.bot.get_file.assert_not_called()

    async def test_reservations_count_towards_capacity(self) -> None:
        manager = JobManager(bot(), settings(queue_size=1), SimpleNamespace(), media())
        manager.reserve(1)
        with self.assertRaises(AppError) as caught:
            manager.reserve(2)
        self.assertEqual(caught.exception.code, "queue_full")
        await manager.close()
        self.assertFalse(manager.active_users)

    async def test_help_and_router(self) -> None:
        router = create_router(self.settings, self.manager)
        msg = message()
        await router.message.handlers[0].callback(msg)
        self.assertIn("AssemblyAI", msg.answer.call_args.args[0])
        self.assertIsNone(msg.answer.call_args.kwargs["parse_mode"])

    def test_invalid_document(self) -> None:
        with self.assertRaises(AppError):
            attachment_from_message(
                message("document", file_name="x.txt", mime_type="text/plain"), self.settings
            )
