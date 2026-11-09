import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from errors import AppError
from handlers import receive_media
from media import MediaInfo
from processing import Attachment, Job, JobManager
from tests.helpers import bot, media, message, settings, transcript
from transcribe import Transcript


class ProcessingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.bot = bot()
        self.media = media()
        self.service = SimpleNamespace(transcribe=AsyncMock(return_value=transcript()))
        self.manager = JobManager(self.bot, settings(workers=1), self.service, self.media)

    async def asyncTearDown(self) -> None:
        await self.manager.close()

    async def submit_and_wait(self, msg: SimpleNamespace | None = None) -> SimpleNamespace:
        msg = msg or message()
        await receive_media(msg, self.manager.settings, self.manager)
        self.manager.start()
        await asyncio.wait_for(self.manager.queue.join(), timeout=5)
        return msg

    async def test_end_to_end_voice_and_cleanup(self) -> None:
        msg = await self.submit_and_wait()
        self.service.transcribe.assert_awaited_once()
        path = self.service.transcribe.call_args.args[0]
        self.assertFalse(path.parent.exists())
        self.assertEqual(
            self.bot.send_message.call_args.args[1], "[00:00:00] Спикер A:\nПривет <мир> & друзья"
        )
        self.assertIsNone(self.bot.send_message.call_args.kwargs["parse_mode"])
        self.assertEqual(self.bot.send_document.call_args.args[1].filename, "transcript.srt")
        self.assertFalse(self.manager.active_users)
        self.assertIn("Готово", msg.answer.return_value.edit_text.call_args.args[0])

    async def test_video_extracts_before_transcription(self) -> None:
        await self.submit_and_wait(message("video"))
        self.media.extract.assert_awaited_once()
        path = self.service.transcribe.call_args.args[0]
        self.assertEqual(path.name, "audio.wav")
        self.assertFalse(path.exists())

    async def test_document_detected_as_video(self) -> None:
        self.media.inspect.return_value = MediaInfo(2, True)
        await self.submit_and_wait(message("document", file_name="video.mp4"))
        self.media.extract.assert_awaited_once()

    async def test_errors_release_user_and_keep_worker_alive(self) -> None:
        self.media.inspect.side_effect = [
            AppError("media", "Повреждённый файл"),
            MediaInfo(2, False),
        ]
        await receive_media(message(), self.manager.settings, self.manager)
        await receive_media(message(user=2), self.manager.settings, self.manager)
        self.manager.start()
        await asyncio.wait_for(self.manager.queue.join(), 5)
        self.service.transcribe.assert_awaited_once()
        self.assertFalse(self.manager.active_users)
        self.assertFalse(self.manager.tasks[0].done())
        for call in self.media.inspect.call_args_list:
            self.assertFalse(call.args[0].parent.exists())

    async def test_no_audio_and_service_error_cleanup(self) -> None:
        for phase, error in (
            ("media", AppError("no_audio", "Нет аудиодорожки")),
            ("service", AppError("service", "Ошибка сервиса")),
        ):
            with self.subTest(phase=phase):
                self.media.inspect.side_effect = error if phase == "media" else None
                self.service.transcribe.side_effect = error if phase == "service" else None
                msg = message()
                job = Job(1, 1, Attachment("id", "voice"), msg.answer.return_value, "test-job")
                with self.assertRaises(AppError):
                    await self.manager._process(job)
                self.assertFalse(self.media.inspect.call_args.args[0].parent.exists())

    async def test_actual_size_bound_stops_download(self) -> None:
        self.manager.settings = settings(max_file_bytes=5)
        self.bot.get_file.return_value.file_size = None

        async def download(file_path: str, destination: object, **kwargs: object) -> None:
            destination.write(b"1234")
            destination.write(b"56")

        self.bot.download_file.side_effect = download
        await self.submit_and_wait(message(file_size=None))
        self.service.transcribe.assert_not_called()
        self.media.inspect.assert_not_called()
        self.assertFalse(self.manager.active_users)

    async def test_get_file_size_limit_before_download(self) -> None:
        self.bot.get_file.return_value.file_size = 20_000_001
        await self.submit_and_wait(message(file_size=None))
        self.bot.download_file.assert_not_called()

    async def test_empty_download(self) -> None:
        self.bot.download_file.side_effect = None
        await self.submit_and_wait()
        self.media.inspect.assert_not_called()

    async def test_empty_result_no_documents(self) -> None:
        self.service.transcribe.return_value = Transcript("")
        msg = await self.submit_and_wait()
        self.bot.send_document.assert_not_called()
        self.bot.send_message.assert_not_called()
        self.assertIn("не обнаружена", msg.answer.return_value.edit_text.call_args.args[0])

    async def test_long_text_utf8_txt_and_no_fabricated_srt(self) -> None:
        self.service.transcribe.return_value = Transcript("😀" * 2049)
        msg = await self.submit_and_wait()
        self.bot.send_message.assert_not_called()
        document = self.bot.send_document.call_args.args[1]
        self.assertEqual(document.filename, "transcript.txt")
        self.assertEqual(document.data.decode("utf-8"), "😀" * 2049)
        self.assertIn("SRT", msg.answer.return_value.edit_text.call_args.args[0])

    async def test_delivery_retry_does_not_transcribe_again(self) -> None:
        self.bot.send_message.side_effect = [TimeoutError(), None]
        await self.submit_and_wait()
        self.assertEqual(self.bot.send_message.await_count, 2)
        self.service.transcribe.assert_awaited_once()

    async def test_delivery_failure_is_bounded(self) -> None:
        self.bot.send_document.side_effect = RuntimeError("private-token")
        msg = await self.submit_and_wait()
        self.service.transcribe.assert_awaited_once()
        self.assertEqual(self.bot.send_document.await_count, 1)
        self.assertIn("доставить", msg.answer.return_value.edit_text.call_args.args[0])
        self.assertNotIn("private-token", str(msg.answer.return_value.edit_text.call_args))

    async def test_download_timeout_and_cleanup(self) -> None:
        self.manager.settings = settings(download_timeout=0.02)
        paths = []

        async def download(file_path: str, destination: object, **kwargs: object) -> None:
            paths.append(Path(destination.raw.name))
            await asyncio.Event().wait()

        self.bot.download_file.side_effect = download
        msg = await self.submit_and_wait()
        self.assertFalse(paths[0].parent.exists())
        self.assertIn("время скачивания", msg.answer.return_value.edit_text.call_args.args[0])

    async def test_shutdown_cancels_current_cleans_queued_and_releases_limits(self) -> None:
        entered = asyncio.Event()
        paths = []

        async def transcribe(path: Path) -> Transcript:
            paths.append(path)
            entered.set()
            await asyncio.Event().wait()

        self.service.transcribe.side_effect = transcribe
        await receive_media(message(), self.manager.settings, self.manager)
        await receive_media(message(user=2), self.manager.settings, self.manager)
        self.manager.start()
        await asyncio.wait_for(entered.wait(), 5)
        await self.manager.close()
        await asyncio.wait_for(self.manager.queue.join(), 1)
        self.assertFalse(paths[0].parent.exists())
        self.assertFalse(self.manager.active_users)
        self.assertFalse(self.manager.reservations)
        self.assertTrue(self.manager.queue.empty())

    async def test_fixed_worker_count_bounds_concurrency(self) -> None:
        entered = asyncio.Event()

        async def transcribe(path: Path) -> Transcript:
            entered.set()
            await asyncio.Event().wait()

        self.service.transcribe.side_effect = transcribe
        for user in (1, 2, 3):
            await receive_media(message(user=user), self.manager.settings, self.manager)
        self.manager.start()
        await asyncio.wait_for(entered.wait(), 5)
        self.assertEqual(self.service.transcribe.await_count, 1)
        self.assertEqual(self.manager.queue.qsize(), 2)
