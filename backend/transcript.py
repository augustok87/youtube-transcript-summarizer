"""Transcript extraction — captions (YouTube) with Whisper fallback, multi-platform support."""

import json
import logging
import tempfile
import time
from pathlib import Path

from youtube_transcript_api import YouTubeTranscriptApi

from backend.config import CACHE_TTL_HOURS, WHISPER_MODEL
from backend.models import TranscriptResponse
from backend.utils import clean_transcript_text, parse_video_url

logger = logging.getLogger(__name__)

# --- Disk-persisted transcript cache ---

_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "transcripts.json"
_CACHE_TTL_SECONDS: float = CACHE_TTL_HOURS * 3600

# In-memory mirror: "platform:video_id" → (TranscriptResponse, timestamp)
_cache: dict[str, tuple[TranscriptResponse, float]] = {}


def _load_cache_from_disk() -> None:
    """Load cached transcripts from disk into memory on startup."""
    if not _CACHE_FILE.exists():
        return
    try:
        raw = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        now = time.time()
        loaded = 0
        for key, entry in raw.items():
            cached_at = entry["cached_at"]
            if now - cached_at < _CACHE_TTL_SECONDS:
                response = TranscriptResponse(**entry["data"])
                _cache[key] = (response, cached_at)
                loaded += 1
        logger.info(f"Loaded {loaded} cached transcripts from disk ({len(raw) - loaded} expired, pruned)")
    except Exception as e:
        logger.warning(f"Could not load transcript cache from disk: {e}")


def _save_cache_to_disk() -> None:
    """Persist the in-memory cache to disk as JSON."""
    _CACHE_DIR.mkdir(exist_ok=True)
    serializable: dict[str, dict] = {}
    for key, (response, cached_at) in _cache.items():
        serializable[key] = {
            "data": response.model_dump(),
            "cached_at": cached_at,
        }
    try:
        _CACHE_FILE.write_text(
            json.dumps(serializable, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.error(f"Could not save transcript cache to disk: {e}")


# Load cache on module import
_load_cache_from_disk()


class TranscriptError(Exception):
    """Raised when transcript extraction fails entirely."""

    def __init__(self, error_code: str, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(message)


async def extract_transcript(url: str) -> TranscriptResponse:
    """Extract transcript from a video URL. Returns cached result if available."""
    parsed = parse_video_url(url)
    if not parsed:
        raise TranscriptError("invalid_url", f"Unsupported URL: {url}")

    platform, video_id = parsed
    cache_key = f"{platform}:{video_id}"

    # Check cache
    if cache_key in _cache:
        cached_response, cached_at = _cache[cache_key]
        if time.time() - cached_at < _CACHE_TTL_SECONDS:
            logger.info(f"Cache hit for {cache_key} (age: {(time.time() - cached_at) / 3600:.1f}h)")
            return cached_response
        else:
            del _cache[cache_key]
            logger.info(f"Cache expired for {cache_key}")

    title, duration = await _fetch_video_metadata(url)

    if platform == "youtube":
        try:
            result = await _extract_captions(video_id, title, duration)
        except TranscriptError:
            raise
        except Exception as e:
            logger.info(f"Captions unavailable for {video_id}: {type(e).__name__}: {e}. Trying Whisper fallback.")
            result = await _extract_with_whisper(url, video_id, title, duration, platform)
    else:
        # Non-YouTube platforms: Whisper only
        logger.info(f"Platform '{platform}' — using Whisper transcription for {video_id}")
        result = await _extract_with_whisper(url, video_id, title, duration, platform)

    # Store in cache (memory + disk)
    _cache[cache_key] = (result, time.time())
    _save_cache_to_disk()
    logger.info(f"Cached transcript for {cache_key} (persisted to disk)")
    return result


async def _fetch_video_metadata(url: str) -> tuple[str, float]:
    """Fetch video title and duration using yt-dlp (metadata only, no download)."""
    import yt_dlp

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "no_check_extensions": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                raise TranscriptError("video_not_found", f"Could not find video: {url}")
            title = info.get("title", "Unknown")
            duration = float(info.get("duration", 0))
            return title, duration
    except yt_dlp.utils.DownloadError as e:
        error_msg = str(e).lower()
        if "private" in error_msg or "unavailable" in error_msg or "age" in error_msg:
            raise TranscriptError("video_unavailable", f"Video is private, age-restricted, or unavailable: {url}")
        raise TranscriptError("video_not_found", f"Could not find video: {url}")


async def _extract_captions(video_id: str, title: str, duration: float) -> TranscriptResponse:
    """Extract captions using youtube-transcript-api v1.2+ (YouTube only)."""
    ytt_api = YouTubeTranscriptApi()

    result = ytt_api.fetch(video_id)

    snippets = result.snippets
    full_text = " ".join(s.text for s in snippets)
    full_text = clean_transcript_text(full_text)

    language = result.language_code
    logger.info(f"Extracted captions for {video_id}: {len(snippets)} segments, language={language}")

    return TranscriptResponse(
        video_id=video_id,
        title=title,
        transcript=full_text,
        language=language,
        source="captions",
        duration_seconds=duration,
        platform="youtube",
    )


async def _extract_with_whisper(
    url: str, video_id: str, title: str, duration: float, platform: str
) -> TranscriptResponse:
    """Download audio and transcribe with faster-whisper."""
    import yt_dlp
    from faster_whisper import WhisperModel

    # Use a safe filename (video_id may contain slashes for Rumble slugs)
    safe_name = video_id.replace("/", "_").replace("\\", "_")[:60]
    tmpdir = tempfile.mkdtemp()
    audio_path = Path(tmpdir) / f"{safe_name}.mp3"

    try:
        opts = {
            "format": "bestaudio[ext!=tar]/best[ext!=tar]/bestaudio/best",
            "outtmpl": str(audio_path.with_suffix(".%(ext)s")),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "64",
            }],
            "quiet": True,
            "no_warnings": True,
            # Rumble may serve files with unusual extensions (.tar) — skip extension safety check
            "no_check_extensions": True,
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        # Find the actual output file (extension may vary)
        actual_files = list(Path(tmpdir).glob(f"{safe_name}.*"))
        if not actual_files:
            raise TranscriptError("whisper_failure", "Audio download produced no output file.")
        audio_file = actual_files[0]

        if duration > 7200:
            logger.warning(f"Video {video_id} is {duration/3600:.1f}h — Whisper may use significant memory.")

        model = WhisperModel(WHISPER_MODEL, compute_type="int8")
        segments_iter, info = model.transcribe(str(audio_file))

        segments = []
        texts = []
        for seg in segments_iter:
            segments.append({"text": seg.text, "start": seg.start, "duration": seg.end - seg.start})
            texts.append(seg.text)

        full_text = clean_transcript_text(" ".join(texts))
        language = info.language

        logger.info(f"Whisper transcription for {video_id}: {len(segments)} segments, language={language}")

        return TranscriptResponse(
            video_id=video_id,
            title=title,
            transcript=full_text,
            language=language,
            source="whisper",
            duration_seconds=duration,
            platform=platform,
        )
    except TranscriptError:
        raise
    except Exception as e:
        raise TranscriptError("whisper_failure", f"Whisper transcription failed: {e}")
    finally:
        for f in Path(tmpdir).iterdir():
            f.unlink(missing_ok=True)
        Path(tmpdir).rmdir()
