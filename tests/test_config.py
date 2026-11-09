import os
import runpy
import shutil
import unittest
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import config


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(TemporaryDirectory(prefix="transcribe-config-test-")))
        self.dotenv_path = self.root / ".env"
        self.module_path = self.root / "config.py"
        shutil.copyfile(config.__file__, self.module_path)
        self.enterContext(patch.dict(os.environ, {}, clear=True))

    def write_dotenv(self) -> None:
        self.dotenv_path.write_text(
            'BOT_TOKEN="file-token"\n'
            "ASSEMBLYAI_API_KEY='file-key'\n"
            "ALLOWED_USER_IDS=11,22 # allowed users\n"
            "WORKERS=3\n",
            encoding="utf-8-sig",
        )

    def test_loads_dotenv_independently_of_working_directory(self) -> None:
        self.write_dotenv()
        other_directory = self.root / "other"
        other_directory.mkdir()
        with chdir(other_directory):
            module = runpy.run_path(str(self.module_path))
        self.assertEqual(module["TOKEN"], "file-token")
        self.assertEqual(module["API_KEY"], "file-key")
        self.assertEqual(module["ALLOWED_USER_IDS"], {11, 22})
        self.assertEqual(module["WORKERS"], 2)

    def test_existing_environment_takes_precedence(self) -> None:
        self.write_dotenv()
        os.environ.update(BOT_TOKEN="environment-token", ALLOWED_USER_IDS="33", WORKERS="4")
        module = runpy.run_path(str(self.module_path))
        self.assertEqual(module["TOKEN"], "environment-token")
        self.assertEqual(module["API_KEY"], "file-key")
        self.assertEqual(module["ALLOWED_USER_IDS"], {33})
        self.assertEqual(module["WORKERS"], 2)

    def test_empty_allowlist_is_not_replaced_by_dotenv(self) -> None:
        self.write_dotenv()
        os.environ["ALLOWED_USER_IDS"] = ""
        module = runpy.run_path(str(self.module_path))
        self.assertEqual(module["ALLOWED_USER_IDS"], set())

    def test_missing_dotenv_and_default_values(self) -> None:
        module = runpy.run_path(str(self.module_path))
        self.assertIsNone(module["TOKEN"])
        self.assertIsNone(module["API_KEY"])
        self.assertEqual(module["ALLOWED_USER_IDS"], set())
        self.assertEqual(module["MAX_FILE_BYTES"], 20_000_000)
        self.assertEqual(module["MAX_DURATION_SECONDS"], 3600)
        self.assertEqual(module["WORKERS"], 2)
        self.assertEqual(module["FFMPEG"], "ffmpeg")

    def test_constants_ignore_environment_and_ids_have_no_range_validation(self) -> None:
        values = {
            "ALLOWED_USER_IDS": " -1,0,1,1 ",
            "MAX_FILE_BYTES": "30000000",
            "MAX_DURATION_SECONDS": "7200",
            "WORKERS": "0",
            "QUEUE_SIZE": "-1",
            "DOWNLOAD_TIMEOUT": "1",
            "MEDIA_TIMEOUT": "2",
            "TRANSCRIPTION_TIMEOUT": "3",
            "REQUEST_TIMEOUT": "4",
            "DELIVERY_ATTEMPTS": "5",
            "FFMPEG": "custom-ffmpeg",
            "FFPROBE": "custom-ffprobe",
        }
        os.environ.update(values)
        module = runpy.run_path(str(self.module_path))
        self.assertEqual(module["ALLOWED_USER_IDS"], {-1, 0, 1})
        constants = {
            "MAX_FILE_BYTES": 20_000_000,
            "MAX_DURATION_SECONDS": 3600,
            "WORKERS": 2,
            "QUEUE_SIZE": 10,
            "DOWNLOAD_TIMEOUT": 120,
            "MEDIA_TIMEOUT": 180,
            "TRANSCRIPTION_TIMEOUT": 1800,
            "REQUEST_TIMEOUT": 60,
            "DELIVERY_ATTEMPTS": 2,
            "FFMPEG": "ffmpeg",
            "FFPROBE": "ffprobe",
        }
        for name, expected in constants.items():
            with self.subTest(name=name):
                self.assertEqual(module[name], expected)
