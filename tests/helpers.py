from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

from media import MediaInfo
from transcribe import Transcript, Utterance


def settings(**overrides: object) -> ModuleType:
    result = ModuleType("test_config")
    result.__dict__.update(
        TOKEN="123456:TEST_ONLY_TOKEN",
        API_KEY="test-only-key",
        ALLOWED_USER_IDS={1, 2, 3},
        MAX_FILE_BYTES=20_000_000,
        MAX_DURATION_SECONDS=3600,
        WORKERS=2,
        QUEUE_SIZE=10,
        DOWNLOAD_TIMEOUT=120,
        MEDIA_TIMEOUT=180,
        TRANSCRIPTION_TIMEOUT=1800,
        REQUEST_TIMEOUT=60,
        DELIVERY_ATTEMPTS=2,
        FFMPEG="ffmpeg",
        FFPROBE="ffprobe",
    )
    result.__dict__.update({name.upper(): value for name, value in overrides.items()})
    return result


def message(kind: str = "voice", user: int = 1, **metadata: object) -> SimpleNamespace:
    attachment = SimpleNamespace(
        **{
            "file_id": "test-file-id",
            "file_size": 4,
            "duration": 2,
            "file_name": "recording.mp3",
            "mime_type": "audio/mpeg",
            **metadata,
        }
    )
    status = SimpleNamespace(edit_text=AsyncMock())
    return SimpleNamespace(
        chat=SimpleNamespace(id=user, type="private"),
        from_user=SimpleNamespace(id=user, is_bot=False),
        voice=attachment if kind == "voice" else None,
        audio=attachment if kind == "audio" else None,
        video=attachment if kind == "video" else None,
        document=attachment if kind == "document" else None,
        answer=AsyncMock(return_value=status),
    )


def bot() -> SimpleNamespace:
    async def download(file_path: str, destination: object, **kwargs: object) -> None:
        destination.write(b"data")

    return SimpleNamespace(
        get_file=AsyncMock(return_value=SimpleNamespace(file_size=4, file_path="local-test-file")),
        download_file=AsyncMock(side_effect=download),
        send_message=AsyncMock(),
        send_document=AsyncMock(),
    )


def media() -> SimpleNamespace:
    async def extract(source: Path, destination: Path) -> Path:
        destination.write_bytes(b"wav")
        return destination

    return SimpleNamespace(
        inspect=AsyncMock(return_value=MediaInfo(2, False)),
        extract=AsyncMock(side_effect=extract),
    )


def transcript() -> Transcript:
    return Transcript(
        "Привет <мир> & друзья", "ru", (Utterance("Привет <мир> & друзья", 100, 1000, "A"),)
    )
