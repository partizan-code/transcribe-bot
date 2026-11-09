"""In-memory admission control, workers, job cleanup, and Telegram delivery."""

import asyncio
import io
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from types import ModuleType
from typing import Any
from uuid import uuid4

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError
from aiogram.types import BufferedInputFile
from loguru import logger

from errors import AppError
from formatters import format_srt, format_text
from media import MediaProcessor
from transcribe import Transcript, TranscriptionService


@dataclass(frozen=True)
class Attachment:
    file_id: str
    kind: str
    size: int | None = None
    duration: int | None = None


@dataclass(frozen=True)
class Job:
    user_id: int
    chat_id: int
    attachment: Attachment
    status: Any
    job_id: str


class LimitedWriter(io.BufferedWriter):
    def __init__(self, path: Path, limit: int) -> None:
        super().__init__(io.FileIO(path, "w"))
        self.limit = limit
        self.total = 0

    def write(self, data: bytes) -> int:
        if self.total + len(data) > self.limit:
            raise AppError("size", "Размер файла превышает допустимый лимит.")
        written = super().write(data)
        self.total += written
        return written


def telegram_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class JobManager:
    def __init__(
        self,
        bot: Any,
        settings: ModuleType,
        transcriber: TranscriptionService,
        media: MediaProcessor,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self.transcriber = transcriber
        self.media = media
        self.queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=settings.QUEUE_SIZE)
        self.active_users: set[int] = set()
        self.reservations: set[int] = set()
        self.tasks: list[asyncio.Task[None]] = []
        self.closing = False

    def reserve(self, user_id: int) -> None:
        # All admission mutations run without await on the polling event loop.
        if self.closing:
            raise AppError("closing", "Бот останавливается. Отправьте запись позднее.")
        if user_id in self.active_users:
            raise AppError("duplicate", "Ваша предыдущая запись ещё обрабатывается.")
        if self.queue.qsize() + len(self.reservations) >= self.settings.QUEUE_SIZE:
            raise AppError("queue_full", "Очередь заполнена. Попробуйте позднее.")
        self.active_users.add(user_id)
        self.reservations.add(user_id)

    def release(self, user_id: int) -> None:
        self.reservations.discard(user_id)
        self.active_users.discard(user_id)

    def enqueue(self, user_id: int, chat_id: int, attachment: Attachment, status: Any) -> None:
        if self.closing or user_id not in self.reservations:
            raise AppError("closing", "Бот останавливается. Отправьте запись позднее.")
        self.queue.put_nowait(Job(user_id, chat_id, attachment, status, uuid4().hex))
        self.reservations.remove(user_id)

    def start(self) -> None:
        if self.tasks or self.closing:
            raise RuntimeError("Workers already started or closed")
        self.tasks = [asyncio.create_task(self._worker()) for _ in range(self.settings.WORKERS)]

    async def close(self) -> None:
        self.closing = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        self.reservations.clear()
        self.active_users.clear()

    async def _status(self, job: Job, text: str) -> None:
        try:
            async with asyncio.timeout(self.settings.REQUEST_TIMEOUT):
                await job.status.edit_text(text, parse_mode=None)
        except Exception as exc:
            logger.warning("job={} stage=status error={}", job.job_id, type(exc).__name__)

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            started = monotonic()
            try:
                await self._process(job)
                logger.info(
                    "job={} stage=finished elapsed={:.2f}s", job.job_id, monotonic() - started
                )
            except AppError as exc:
                logger.warning(
                    "job={} stage=failed code={} elapsed={:.2f}s",
                    job.job_id,
                    exc.code,
                    monotonic() - started,
                )
                await self._status(job, str(exc))
            except Exception as exc:
                logger.error(
                    "job={} stage=failed error={} elapsed={:.2f}s",
                    job.job_id,
                    type(exc).__name__,
                    monotonic() - started,
                )
                await self._status(
                    job, "Не удалось обработать запись. Обратитесь к администратору."
                )
            finally:
                self.release(job.user_id)
                self.queue.task_done()

    async def _process(self, job: Job) -> None:
        with TemporaryDirectory(prefix="transcribe-") as directory:
            source = Path(directory) / "input.media"
            logger.info("job={} stage=download", job.job_id)
            await self._status(job, "Скачиваю")
            await self._download(job, source)
            logger.info("job={} stage=probe", job.job_id)
            info = await self.media.inspect(source)
            audio = source
            if info.has_video or job.attachment.kind == "video":
                logger.info("job={} stage=extract", job.job_id)
                await self._status(job, "Извлекаю аудио")
                audio = await self.media.extract(source, Path(directory) / "audio.wav")
            logger.info("job={} stage=transcribe", job.job_id)
            await self._status(job, "Распознаю")
            result = await self.transcriber.transcribe(audio)
            logger.info("job={} stage=deliver", job.job_id)
            await self._deliver(job, result)

    async def _download(self, job: Job, path: Path) -> None:
        try:
            async with asyncio.timeout(self.settings.DOWNLOAD_TIMEOUT):
                file = await self.bot.get_file(job.attachment.file_id)
                if file.file_size is not None and file.file_size > self.settings.MAX_FILE_BYTES:
                    raise AppError("size", "Размер файла превышает допустимый лимит.")
                if not file.file_path:
                    raise AppError("download", "Telegram не предоставил файл для скачивания.")
                with LimitedWriter(path, self.settings.MAX_FILE_BYTES) as destination:
                    await self.bot.download_file(
                        file.file_path,
                        destination=destination,
                        timeout=self.settings.DOWNLOAD_TIMEOUT,
                        seek=False,
                    )
                if path.stat().st_size == 0:
                    raise AppError("media", "Получен пустой файл.")
        except TimeoutError:
            raise AppError("download_timeout", "Превышено время скачивания из Telegram.") from None
        except AppError:
            raise
        except Exception:
            raise AppError("download", "Не удалось скачать файл из Telegram.") from None

    async def _send(self, action: Callable[[], Awaitable[Any]]) -> None:
        for attempt in range(self.settings.DELIVERY_ATTEMPTS):
            try:
                async with asyncio.timeout(self.settings.REQUEST_TIMEOUT):
                    await action()
                return
            except (
                TelegramRetryAfter,
                TelegramNetworkError,
                TelegramServerError,
                TimeoutError,
            ) as exc:
                delay = getattr(exc, "retry_after", 1)
                if attempt + 1 == self.settings.DELIVERY_ATTEMPTS or delay > 10:
                    break
                await asyncio.sleep(delay)
            except Exception:
                break
        raise AppError(
            "delivery",
            "Распознавание завершено, но доставить результат не удалось. "
            "Повторная транскрибация автоматически не запускается.",
        ) from None

    async def _deliver(self, job: Job, result: Transcript) -> None:
        text = format_text(result)
        if not text:
            await self._status(job, "Речь не обнаружена.")
            return
        subtitles = format_srt(result)
        if telegram_length(text) <= 4096:
            await self._send(lambda: self.bot.send_message(job.chat_id, text, parse_mode=None))
        else:
            await self._send(
                lambda: self.bot.send_document(
                    job.chat_id,
                    BufferedInputFile(text.encode("utf-8"), filename="transcript.txt"),
                    caption="Расшифровка в TXT (UTF-8)",
                    parse_mode=None,
                )
            )
        if subtitles.text:
            await self._send(
                lambda: self.bot.send_document(
                    job.chat_id,
                    BufferedInputFile(subtitles.text.encode("utf-8"), filename="transcript.srt"),
                    parse_mode=None,
                )
            )
        await self._status(
            job,
            "Готово. "
            + (
                "Для части текста или всего текста нет корректных временных меток; SRT ограничен или недоступен."
                if subtitles.incomplete
                else "Текст и SRT отправлены."
            ),
        )
