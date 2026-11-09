"""Telegram admission only; long-running work belongs to JobManager."""

import asyncio
from pathlib import PurePath
from types import ModuleType

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from loguru import logger

from errors import AppError
from media import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS
from processing import Attachment, JobManager


def authorized(message: Message, settings: ModuleType) -> bool:
    return (
        message.chat.type == "private"
        and message.from_user is not None
        and not message.from_user.is_bot
        and message.from_user.id in settings.ALLOWED_USER_IDS
    )


def attachment_from_message(message: Message, settings: ModuleType) -> Attachment:
    for kind in ("voice", "audio", "video", "document"):
        item = getattr(message, kind, None)
        if item is not None:
            break
    else:
        raise AppError("unsupported", "Отправьте голосовое сообщение, аудио или видеофайл.")
    if kind == "document":
        suffix = PurePath(item.file_name or "").suffix.lower()
        mime = (item.mime_type or "").lower()
        if suffix not in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS and not mime.startswith(
            ("audio/", "video/")
        ):
            raise AppError(
                "unsupported", "Этот тип документа не поддерживается. Отправьте аудио или видео."
            )
    size = item.file_size
    duration = getattr(item, "duration", None)
    if size is not None and (size <= 0 or size > settings.MAX_FILE_BYTES):
        raise AppError(
            "size", f"Файл должен быть непустым и не больше {settings.MAX_FILE_BYTES} байт."
        )
    if duration is not None and duration > settings.MAX_DURATION_SECONDS:
        raise AppError("duration", "Запись превышает допустимую длительность.")
    return Attachment(item.file_id, kind, size, duration)


async def receive_media(message: Message, settings: ModuleType, manager: JobManager) -> None:
    async with asyncio.timeout(settings.REQUEST_TIMEOUT):
        if not authorized(message, settings):
            await message.answer(
                "Доступ разрешён только участникам allowlist в личном чате.", parse_mode=None
            )
            return
        reserved = False
        try:
            attachment = attachment_from_message(message, settings)
            manager.reserve(message.from_user.id)
            reserved = True
            status = await message.answer("В очереди", parse_mode=None)
            manager.enqueue(message.from_user.id, message.chat.id, attachment, status)
            reserved = False
        except AppError as exc:
            await message.answer(str(exc), parse_mode=None)
        finally:
            if reserved:
                manager.release(message.from_user.id)


def create_router(settings: ModuleType, manager: JobManager) -> Router:
    router = Router(name="transcription")

    @router.message(Command("start", "help"))
    async def help_command(message: Message) -> None:
        text = "Доступ разрешён только участникам allowlist в личном чате."
        if authorized(message, settings):
            text = (
                "Отправьте голосовое сообщение, аудио или видео, в том числе документом.\n"
                f"Лимиты: {settings.MAX_FILE_BYTES} байт, {settings.MAX_DURATION_SECONDS} секунд; "
                "одна незавершённая запись на пользователя.\n"
                "Форматы: MP3, WAV, FLAC, OGG/Opus, M4A/AAC, MP4/MOV, MKV/WebM.\n"
                "Вы получите текст с говорящими и таймкодами и SRT при наличии временных меток.\n"
                "Запись передаётся в AssemblyAI. Временные локальные файлы удаляются; "
                "это не удаляет данные у провайдера. Очередь теряется при перезапуске."
            )
        async with asyncio.timeout(settings.REQUEST_TIMEOUT):
            await message.answer(text, parse_mode=None)

    @router.message(F.voice | F.audio | F.video | F.document)
    async def media_message(message: Message) -> None:
        await receive_media(message, settings, manager)

    @router.message()
    async def unsupported(message: Message) -> None:
        async with asyncio.timeout(settings.REQUEST_TIMEOUT):
            await message.answer(
                "Отправьте голосовое сообщение, аудио или видео. Справка: /help."
                if authorized(message, settings)
                else "Доступ запрещён.",
                parse_mode=None,
            )

    @router.error()
    async def handler_error(event: object) -> bool:
        logger.warning(
            "stage=telegram_handler error={}", type(getattr(event, "exception", None)).__name__
        )
        return True

    return router
