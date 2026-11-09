"""Bounded local media inspection and audio extraction, without shell or network."""

import asyncio
import json
import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from errors import AppError

AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".flac", ".ogg", ".opus", ".m4a", ".aac", ".webm"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm"})
DEMUXERS = "mp3,wav,flac,ogg,mov,matroska,webm,aac"
MAX_EXTRACTED_BYTES = 128_000_000


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_video: bool


async def run_process(args: list[str], timeout: int) -> bytes:
    """Drain bounded stdout, discard private diagnostics, reap on every exit."""
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        raise AppError(
            "media_tools", "Не удалось запустить FFmpeg/ffprobe. Обратитесь к администратору."
        ) from None
    try:
        async with asyncio.timeout(timeout):
            chunks = bytearray()
            while chunk := await process.stdout.read(65536):
                chunks.extend(chunk)
                if len(chunks) > 1_000_000:
                    raise AppError("media", "Слишком сложный или повреждённый медиафайл.")
            await process.wait()
        if process.returncode:
            raise AppError("media", "Файл повреждён или его формат не поддерживается.")
        return bytes(chunks)
    except TimeoutError:
        raise AppError("media_timeout", "Превышено время локальной обработки медиа.") from None
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()


class MediaProcessor:
    def __init__(self, settings: ModuleType) -> None:
        self.settings = settings

    def check_tools(self) -> None:
        for executable in (self.settings.FFMPEG, self.settings.FFPROBE):
            if shutil.which(executable) is None:
                raise AppError(
                    "media_tools", "Установите FFmpeg и ffprobe или задайте FFMPEG/FFPROBE."
                )

    async def inspect(self, path: Path) -> MediaInfo:
        raw = await run_process(
            [
                self.settings.FFPROBE,
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                DEMUXERS,
                "-threads",
                "1",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name,duration:stream_disposition=attached_pic",
                "-of",
                "json",
                str(path.resolve()),
            ],
            self.settings.MEDIA_TIMEOUT,
        )
        try:
            data = json.loads(raw)
            streams = data.get("streams", [])
            if not any(stream.get("codec_type") == "audio" for stream in streams):
                raise AppError("no_audio", "В записи нет аудиодорожки.")
            raw_durations = [data.get("format", {}).get("duration")]
            raw_durations.extend(s.get("duration") for s in streams)
            durations = [float(value) for value in raw_durations if value not in (None, "N/A")]
            durations = [value for value in durations if math.isfinite(value) and value > 0]
            if not durations:
                raise ValueError
            duration = max(durations)
            if duration > self.settings.MAX_DURATION_SECONDS:
                raise AppError("duration", "Запись превышает допустимую длительность.")
            video = any(
                s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
                for s in streams
            )
            return MediaInfo(duration, video)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AppError("media", "Не удалось определить длительность медиафайла.") from None

    async def extract(self, source: Path, destination: Path) -> Path:
        await run_process(
            [
                self.settings.FFMPEG,
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                DEMUXERS,
                "-threads",
                "1",
                "-copyts",
                "-start_at_zero",
                "-i",
                str(source.resolve()),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-af",
                "aresample=async=1:first_pts=0",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-threads",
                "1",
                "-t",
                str(self.settings.MAX_DURATION_SECONDS + 1),
                "-fs",
                str(MAX_EXTRACTED_BYTES),
                str(destination.resolve()),
            ],
            self.settings.MEDIA_TIMEOUT,
        )
        if not destination.exists() or not 44 < destination.stat().st_size < MAX_EXTRACTED_BYTES:
            raise AppError("media", "Не удалось извлечь аудио в пределах ограничений.")
        await self.inspect(destination)
        return destination
