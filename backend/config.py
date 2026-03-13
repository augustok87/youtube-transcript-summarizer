"""Centralized configuration — every env var and constant lives here."""

import base64
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")

logger = logging.getLogger(__name__)


def _require_env(key: str) -> str:
    """Return an env var's value or exit immediately with a clear message."""
    value = os.getenv(key)
    if not value:
        print(f"FATAL: Missing required environment variable: {key}", file=sys.stderr)
        print(f"  Copy .env.example to .env and fill in your keys.", file=sys.stderr)
        sys.exit(1)
    return value


def _setup_youtube_cookies() -> str | None:
    """Decode YOUTUBE_COOKIES_B64 env var to a temp file. Returns file path or None."""
    b64 = os.getenv("YOUTUBE_COOKIES_B64")
    if not b64:
        return None
    try:
        cookies_bytes = base64.b64decode(b64)
        cookies_path = Path("/tmp/youtube_cookies.txt")
        cookies_path.write_bytes(cookies_bytes)
        logger.info("YouTube cookies file written to /tmp/youtube_cookies.txt")
        return str(cookies_path)
    except Exception as e:
        logger.warning(f"Failed to decode YOUTUBE_COOKIES_B64: {e}")
        return None


# --- Required ---
GEMINI_API_KEY: str = _require_env("GEMINI_API_KEY")

# --- Optional with defaults ---
WHISPER_MODEL: str = os.getenv("WHISPER_MODEL", "base")
MAX_CHUNK_WORDS: int = int(os.getenv("MAX_CHUNK_WORDS", "750"))
CHUNK_OVERLAP_WORDS: int = int(os.getenv("CHUNK_OVERLAP_WORDS", "100"))
MAX_CONCURRENT_REQUESTS: int = int(os.getenv("MAX_CONCURRENT_REQUESTS", "3"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
CACHE_TTL_HOURS: int = int(os.getenv("CACHE_TTL_HOURS", "24"))

# --- YouTube cookies (optional, for cloud deployment) ---
YOUTUBE_COOKIES_PATH: str | None = _setup_youtube_cookies()

# --- Constants ---
GEMINI_MODEL: str = "gemini-2.5-flash"
MAX_RETRIES: int = 3
