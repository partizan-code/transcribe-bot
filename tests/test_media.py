import asyncio
import json
import shutil
import struct
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from errors import AppError
from media import MediaProcessor, run_process
from tests.helpers import settings


class MediaTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_validates_streams_duration_and_video(self) -> None:
        processor = MediaProcessor(settings())
        for data, expected in (
            ({"format": {"duration": "2"}, "streams": [{"codec_type": "audio"}]}, None),
            (
                {
                    "format": {"duration": "N/A"},
                    "streams": [{"codec_type": "audio", "duration": "2"}],
                },
                None,
            ),
            ({"format": {"duration": "3601"}, "streams": [{"codec_type": "audio"}]}, "duration"),
            ({"format": {"duration": "2"}, "streams": [{"codec_type": "video"}]}, "no_audio"),
            ({"format": {"duration": "nan"}, "streams": [{"codec_type": "audio"}]}, "media"),
            (
                {
                    "format": {"duration": "2"},
                    "streams": [{"codec_type": "audio", "duration": "4000"}],
                },
                "duration",
            ),
        ):
            with (
                self.subTest(data=data),
                patch("media.run_process", AsyncMock(return_value=json.dumps(data).encode())),
            ):
                if expected:
                    with self.assertRaises(AppError) as caught:
                        await processor.inspect(Path("test"))
                    self.assertEqual(caught.exception.code, expected)
                else:
                    self.assertEqual((await processor.inspect(Path("test"))).duration, 2)

    async def test_cover_image_does_not_trigger_video_conversion(self) -> None:
        data = {
            "format": {"duration": "2"},
            "streams": [
                {"codec_type": "audio"},
                {"codec_type": "video", "disposition": {"attached_pic": 1}},
            ],
        }
        with patch("media.run_process", AsyncMock(return_value=json.dumps(data).encode())):
            self.assertFalse((await MediaProcessor(settings()).inspect(Path("test"))).has_video)

    async def test_missing_tools(self) -> None:
        with patch("media.shutil.which", return_value=None), self.assertRaises(AppError) as caught:
            MediaProcessor(settings()).check_tools()
        self.assertEqual(caught.exception.code, "media_tools")

    async def test_probe_invalid_json(self) -> None:
        with (
            patch("media.run_process", AsyncMock(return_value=b"not-json")),
            self.assertRaises(AppError),
        ):
            await MediaProcessor(settings()).inspect(Path("test"))

    async def test_timeout_and_cancel_kill_and_reap_process(self) -> None:
        for cancelled in (False, True):
            entered = asyncio.Event()

            async def read(size: int, *, entered: asyncio.Event = entered) -> bytes:
                entered.set()
                await asyncio.Event().wait()

            process = SimpleNamespace(
                returncode=None,
                stdout=SimpleNamespace(read=AsyncMock(side_effect=read)),
                kill=Mock(),
                wait=AsyncMock(),
            )
            with patch("media.asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
                task = asyncio.create_task(run_process(["fake"], 10 if cancelled else 0.02))
                await entered.wait()
                if cancelled:
                    task.cancel()
                with self.assertRaises(asyncio.CancelledError if cancelled else AppError):
                    await task
            process.kill.assert_called_once()
            process.wait.assert_awaited_once()


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg/ffprobe unavailable"
)
class LocalMediaIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = TemporaryDirectory(prefix="transcribe-test-")
        self.root = Path(self.directory.name)
        self.processor = MediaProcessor(settings())

    async def asyncTearDown(self) -> None:
        self.directory.cleanup()

    async def test_synthetic_video_extracts_first_track_and_preserves_audio_offset(self) -> None:
        source = self.root / "video.mp4"
        await run_process(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=32x32:r=10:d=2",
                "-itsoffset",
                "0.5",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:duration=2",
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-map",
                "2:a",
                "-c:v",
                "mpeg4",
                "-c:a",
                "aac",
                "-threads",
                "1",
                str(source),
            ],
            30,
        )
        info = await self.processor.inspect(source)
        self.assertTrue(info.has_video)
        self.assertAlmostEqual(info.duration, 2, delta=0.1)
        output = await self.processor.extract(source, self.root / "audio.wav")
        with wave.open(str(output), "rb") as audio:
            self.assertEqual(audio.getframerate(), 16000)
            self.assertEqual(audio.getnchannels(), 1)
            duration = audio.getnframes() / 16000
            samples = struct.unpack(f"<{audio.getnframes()}h", audio.readframes(audio.getnframes()))
        self.assertGreater(duration, 1.4)
        self.assertLess(duration, 1.7)
        self.assertLess(max(abs(x) for x in samples[:4000]), 10)
        tone = samples[12000:16000]
        crossings = sum(a <= 0 < b for a, b in zip(tone, tone[1:], strict=False))
        self.assertAlmostEqual(crossings / 0.25, 440, delta=10)

    async def test_synthetic_audio_stays_audio_and_duration_limit_applies(self) -> None:
        source = self.root / "tone.wav"
        await run_process(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=2",
                str(source),
            ],
            30,
        )
        self.assertFalse((await self.processor.inspect(source)).has_video)
        with self.assertRaises(AppError) as caught:
            await MediaProcessor(settings(max_duration_seconds=1)).inspect(source)
        self.assertEqual(caught.exception.code, "duration")

    async def test_corrupt_file_and_playlist_rejected(self) -> None:
        for data in (b"not-media", b"#EXTM3U\nhttp://127.0.0.1:9/private.wav\n"):
            path = self.root / "input.media"
            path.write_bytes(data)
            with self.assertRaises(AppError) as caught:
                await self.processor.inspect(path)
            self.assertEqual(caught.exception.code, "media")

    async def test_synthetic_silent_video_reports_no_audio(self) -> None:
        source = self.root / "silent.mp4"
        await run_process(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=s=32x32:d=1",
                "-c:v",
                "mpeg4",
                "-threads",
                "1",
                str(source),
            ],
            30,
        )
        with self.assertRaises(AppError) as caught:
            await self.processor.inspect(source)
        self.assertEqual(caught.exception.code, "no_audio")
