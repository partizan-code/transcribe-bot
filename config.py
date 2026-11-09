import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().with_name(".env"), encoding="utf-8-sig")

API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
TOKEN = os.getenv("BOT_TOKEN")
ALLOWED_USER_IDS = {
    int(value) for value in os.getenv("ALLOWED_USER_IDS", "").split(",") if value.strip()
}

MAX_FILE_BYTES = 20_000_000
MAX_DURATION_SECONDS = 3600
WORKERS = 2
QUEUE_SIZE = 10
DOWNLOAD_TIMEOUT = 120
MEDIA_TIMEOUT = 180
TRANSCRIPTION_TIMEOUT = 1800
REQUEST_TIMEOUT = 60
DELIVERY_ATTEMPTS = 2
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
ERROR_LOG_PATH = Path(__file__).resolve().with_name("error.log")
