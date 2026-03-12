# AGENT_INSTRUCTIONS.md
## YouTube Transcript Summarizer — Build Specification

---

## Project Overview

Build a full-stack application that accepts video URLs (YouTube or Rumble), extracts the full transcript (regardless of video length or language), and generates on-demand summaries at multiple granularity levels specified by the user.

---

## Goals

1. Accept a video URL (YouTube or Rumble) as input
2. Extract the full transcript (captions for YouTube, Whisper for Rumble/fallback)
3. Handle videos of any length via chunked processing
4. Generate summaries at user-specified granularity levels
5. Expose a clean API and minimal frontend for interaction
6. Cache transcripts (backend, 24h) and summaries (frontend, 24h) for instant replays

---

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| Transcript extraction | `youtube-transcript-api` | YouTube captions — fast, no download needed |
| Audio fallback / Rumble | `yt-dlp` + `faster-whisper` | `faster-whisper` over OpenAI `whisper` — 4x faster, ~200MB vs ~2GB PyTorch dep. Rumble always uses this path |
| Token counting | `anthropic` SDK's built-in counting | Accurate token counts for chunking decisions |
| LLM summarization | Anthropic Claude API (`claude-sonnet-4-6`) | Latest Sonnet model |
| API server | FastAPI | |
| Frontend | Plain HTML/CSS/JS (no build step) | Served statically by FastAPI — avoids CORS entirely |
| Environment | `.env` file with `ANTHROPIC_API_KEY` | |

---

## Project Structure

```
/
├── backend/
│   ├── main.py                  # FastAPI app, static file serving, CORS
│   ├── transcript.py            # Transcript extraction logic
│   ├── summarizer.py            # Chunking + summarization pipeline
│   ├── models.py                # Pydantic request/response models
│   ├── config.py                # Centralized env var loading + validation
│   └── utils.py                 # Helpers (URL parsing, text cleaning, etc.)
├── frontend/
│   ├── index.html               # Single-page UI
│   ├── style.css                # Minimal styling
│   └── app.js                   # Frontend logic + SSE handling
├── requirements.txt             # Pinned to exact versions
├── .env.example
├── CLAUDE.md
├── AGENT_INSTRUCTIONS.md
└── CHANGELOG.md
```

---

## Module Instructions

### `config.py`

Centralized configuration — every env var and constant lives here.

```python
# Required — fail fast at import time if missing
ANTHROPIC_API_KEY: str

# Optional with defaults
WHISPER_MODEL: str = "base"          # tiny | base | small | medium | large-v3
MAX_CHUNK_WORDS: int = 750           # ~1,000 tokens per chunk
CHUNK_OVERLAP_WORDS: int = 100       # Context preservation between chunks
MAX_CONCURRENT_REQUESTS: int = 3     # Limits simultaneous summarization requests
LOG_LEVEL: str = "INFO"
```

Validate all required vars at module load. If `ANTHROPIC_API_KEY` is missing, raise immediately with a clear message — don't wait for the first API call to fail.

---

### `transcript.py`

This module is responsible for obtaining the full transcript of any YouTube video.

**Primary path — caption extraction:**
- Use `youtube-transcript-api` to fetch available transcripts
- Prefer manually-created transcripts over auto-generated ones when both exist
- Accept any language; do not filter by language code
- Return transcript as a flat list of `{text, start, duration}` dicts, then join into a single string with timestamps preserved

**Fallback path — Whisper transcription:**
- Trigger fallback when `youtube-transcript-api` raises `TranscriptsDisabled`, `NoTranscriptFound`, or any similar exception
- Use `yt-dlp` to download audio only (`--format bestaudio`, output as `.mp3` or `.wav`)
- Pass audio to `faster-whisper` (model configurable via `WHISPER_MODEL` env var)
- For videos >2 hours: log a warning and chunk the audio into 30-minute segments before transcription to manage memory
- Return transcript in the same format as primary path
- Clean up downloaded audio files after transcription (use `try/finally` or a context manager)

**Output contract:**
```python
{
    "video_id": str,             # Extracted from URL, used as cache key
    "title": str,                # Video title (from yt-dlp metadata)
    "transcript": str,           # Full joined text
    "segments": list[dict],      # Original timestamped segments
    "language": str,             # Detected or declared language code
    "source": "captions" | "whisper",
    "duration_seconds": float
}
```

---

### `summarizer.py`

This module handles chunking long transcripts and generating summaries at different granularity levels.

**Chunking strategy:**
- Split transcript into chunks of ~`MAX_CHUNK_WORDS` words each (~1,000 tokens per chunk)
- Preserve sentence boundaries — never cut mid-sentence
- Overlap chunks by ~`CHUNK_OVERLAP_WORDS` words to preserve context across boundaries
- Tag each chunk with its approximate timestamp range if timestamps are available

**Summarization pipeline:**

For each granularity level, implement a two-pass approach for long videos:
1. **Pass 1 — chunk summaries:** Summarize each chunk individually
2. **Pass 2 — final synthesis:** Combine chunk summaries into the final output

For short videos (single chunk), skip Pass 1 and summarize directly.

**Streaming:** Use Claude's streaming API for Pass 2 (the user-facing summary). Forward token events via SSE to the frontend so the user sees the summary as it generates.

**Granularity levels — implement all of the following:**

| Level | Name | Description |
|---|---|---|
| 1 | `one_liner` | Single sentence. The absolute core idea of the video. |
| 2 | `tldr` | 3–5 bullet points. Key takeaways a reader should walk away with. |
| 3 | `short` | 1–2 paragraphs. High-level summary suitable for a preview. |
| 4 | `detailed` | Full structured summary with logical sections. Includes main argument, key points, notable examples, and conclusions. |
| 5 | `chapters` | Break the video into logical chapters with a title and 2–3 sentence summary per chapter. Include approximate timestamps if available. |
| 6 | `custom` | User provides a freeform instruction string (e.g. `"focus only on the technical implementation details"` or `"summarize as if explaining to a 10-year-old"`). Apply that instruction to the summarization prompt. |

**Prompt design guidelines:**
- Always include the original language of the transcript in the system prompt and instruct the model to respond in that same language unless the user explicitly requests a different output language
- For `chapters`, instruct the model to identify natural topic shifts, not just time divisions
- For `custom`, inject the user's instruction as a constraint in the system prompt, not the user turn
- Keep system prompts concise and directive; avoid over-specifying output format in ways that make the model verbose

**Output contract:**
```python
{
    "granularity": str,          # The requested level
    "summary": str,              # The generated summary (markdown formatted)
    "chunk_count": int,          # How many chunks were processed
    "model": str,                # Model used
    "input_tokens": int,
    "output_tokens": int
}
```

---

### `main.py` — FastAPI App

**Endpoints to implement:**

```
POST /api/transcript
  Body: { "url": str }
  Returns: TranscriptResponse (includes video_id for subsequent calls)

POST /api/summarize
  Body: {
    "url": str,
    "granularity": "one_liner" | "tldr" | "short" | "detailed" | "chapters" | "custom",
    "custom_instruction": str | null,   # required when granularity = "custom"
    "output_language": str | null       # optional ISO 639-1 code, e.g. "en", "es"
  }
  Returns: SSE stream (text/event-stream) with summary tokens, then a final JSON event

GET /api/health
  Returns: { "status": "ok" }
```

**Static file serving:**
- Mount `frontend/` as static files at `/` — serves `index.html`, `app.js`, `style.css`
- This eliminates CORS issues entirely since API and frontend share the same origin

**Concurrency control:**
- Use an `asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)` to limit simultaneous LLM calls
- If the semaphore is full, return HTTP 503 with a `Retry-After` header

**Error handling:**
- Return structured JSON errors with `error_code` and `message` for all failure cases
- Specific error codes for: `invalid_url`, `video_not_found`, `video_unavailable` (age-restricted, private, live), `transcripts_disabled`, `whisper_failure`, `llm_error`, `rate_limited`, `server_busy`
- Never return raw Python exceptions or stack traces to the client

---

### `models.py`

Define all Pydantic v2 models here. Keep request and response schemas strict — use `model_config = ConfigDict(extra="forbid")` to reject unknown fields.

---

## Environment Variables

```env
ANTHROPIC_API_KEY=          # Required
WHISPER_MODEL=base          # Optional: tiny | base | small | medium | large-v3
MAX_CHUNK_WORDS=750         # Optional: tune chunking behavior
MAX_CONCURRENT_REQUESTS=3   # Optional: limit parallel LLM calls
LOG_LEVEL=INFO              # Optional
```

---

## Implementation Order

Build in this sequence to enable incremental testing:

1. `config.py` — env var loading and validation
2. `transcript.py` — caption path only, test with a short video
3. `transcript.py` — Whisper fallback path
4. `summarizer.py` — single-chunk summarization for all granularity levels
5. `summarizer.py` — multi-chunk pipeline for long videos
6. `summarizer.py` — streaming support for Pass 2
7. `main.py` — FastAPI endpoints wiring it all together, static file serving
8. `frontend/` — UI (URL input, granularity selector, SSE summary display)

---

## Testing Checkpoints

Use these videos to validate behavior at each stage:

| Test case | What it validates |
|---|---|
| Any short video (<5 min) with English captions | Happy path, caption extraction, all granularity levels |
| Any video >1 hour | Chunking pipeline, multi-pass summarization |
| A video in a non-English language (e.g. Spanish, Japanese) | Language detection, multilingual summarization |
| A video with captions disabled | Whisper fallback |
| An invalid or private URL | Error handling |
| Two simultaneous requests | Concurrency control |

---

## Code Quality Requirements

- All functions must have type hints and docstrings
- Use `async`/`await` throughout FastAPI route handlers
- Log all transcript source decisions (captions vs. Whisper) and chunk counts at `INFO` level
- Log full API errors (with status codes) at `ERROR` level
- No hardcoded API keys or model names outside of `config.py` or environment variables
- `requirements.txt` must be pinned to exact versions

---

## Frontend Requirements

Keep the frontend minimal but functional. No build step — plain HTML/CSS/JS.

- Single-page interface
- URL input field with a submit button
- Granularity selector (dropdown or button group for the 6 levels)
- Text area for custom instruction (shown conditionally when `custom` is selected)
- Loading state with progress indication (transcript extraction → summarizing → streaming)
- Summary output rendered as Markdown (use a lightweight lib like `marked.js` via CDN)
- Stream the summary in real-time via SSE — text appears as it generates
- Show video metadata: title, duration, transcript source (captions vs. Whisper)

---

## Known Constraints & Edge Cases to Handle

- **Age-restricted videos:** `youtube-transcript-api` will fail — surface a clear `video_unavailable` error
- **Live streams:** Transcripts are unavailable during live streams — detect and reject with `video_unavailable`
- **Very short videos (<30s):** May produce trivial transcripts — still process normally, don't special-case
- **Whisper on long audio (>2h):** Memory-intensive. Chunk audio into 30-minute segments before transcription
- **Rate limiting:** If the Claude API returns a 429, implement exponential backoff with up to 3 retries before failing
- **Concurrent requests:** Semaphore prevents overwhelming the LLM API; excess requests get HTTP 503

---

## Implemented Enhancements (Beyond Original Spec)

- **Multi-platform support:** YouTube + Rumble (via `parse_video_url()` in `utils.py`)
- **Backend transcript cache:** In-memory TTL cache (24h) keyed by `platform:video_id`
- **Frontend summary cache:** localStorage-based, same video + same granularity = instant display
- **Search history:** Last 25 searches with thumbnails, one-click re-run, cached summaries
- **SSE newline fix:** Multi-line data uses proper SSE framing (multiple `data:` lines per event)
- **Sandwich prompting:** Format reminder before AND after transcript in user messages

## Out of Scope (Do Not Build)

- User authentication or accounts
- Persistent storage or database
- Playlist or batch URL processing
- Browser extension
