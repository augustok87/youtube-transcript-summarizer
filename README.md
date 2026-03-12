# Video Transcript Summarizer

A full-stack app that extracts video transcripts and generates AI-powered summaries at multiple granularity levels, streamed in real time. Supports **YouTube** and **Rumble**.

**Backend:** Python 3.12 / FastAPI
**Frontend:** Plain HTML / CSS / JS (no build step)
**AI:** Google Gemini API (`gemini-2.5-flash`) with SSE streaming
**Transcript:** youtube-transcript-api (YouTube captions) with yt-dlp + faster-whisper fallback (all platforms)

---

## Quick Start

### Prerequisites

- **Python 3.11+** (3.12 recommended)
- **ffmpeg** — required for Whisper audio fallback (`brew install ffmpeg` on macOS)
- **Gemini API key** — get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)

### Setup

```bash
# Clone and enter project
cd youtube-transcript-summarizer

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY
```

### Run

```bash
uvicorn backend.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser.

---

## Supported Platforms

| Platform | Transcript Method | Speed |
|----------|-------------------|-------|
| **YouTube** | Captions (primary), Whisper fallback | Fast (captions) or slow (Whisper) |
| **Rumble** | Whisper only (no caption API) | Slower — downloads audio + local transcription |

## How It Works

### 1. Transcript Extraction

When you submit a video URL:

1. **Platform detection** — Parses the URL to identify YouTube or Rumble and extract a video ID.
2. **YouTube: captions first** — Uses `youtube-transcript-api` to pull existing captions/subtitles (fast, no download needed).
3. **Whisper fallback** — If no captions exist (YouTube) or the platform has no caption API (Rumble), downloads audio via `yt-dlp` and transcribes with `faster-whisper` locally.
4. **30-day disk cache** — Transcripts are cached to `.cache/transcripts.json` keyed by `platform:video_id`. Survives server restarts. Re-summarizing the same video with a different style skips extraction entirely.

### 2. Summarization Pipeline

For short transcripts (single chunk):
- Sends transcript directly to Gemini with format instructions (sandwich pattern — format reminder before AND after the content).

For long transcripts (multi-chunk):
- **Pass 1:** Each ~750-word chunk is summarized independently.
- **Pass 2:** Chunk summaries are synthesized into the final output with the requested format, streamed via SSE.

**Note:** On the Gemini free tier (5 RPM), long videos with many chunks will take a few minutes for Pass 1 due to rate pacing. Short videos (single chunk) are fast.

### 3. Real-Time Streaming

The summary streams to the browser token-by-token using Server-Sent Events (SSE). You see the output build up live, rendered as markdown.

---

## Summary Styles

| Style | Output |
|-------|--------|
| **One-liner** | A single sentence capturing the core idea |
| **TL;DR** | 3–5 bullet points with bolded key concepts |
| **Short** | 2 focused paragraphs |
| **Detailed** | Structured summary with headings, prose, and key takeaways |
| **Chapters** | Logical chapters with timestamps and descriptions |
| **Custom** | Your own instruction (e.g., "Explain like I'm 10 years old") |

---

## Project Structure

```
youtube-transcript-summarizer/
├── backend/
│   ├── main.py            # FastAPI app, endpoints, static serving
│   ├── config.py          # Environment variables & constants
│   ├── models.py          # Pydantic v2 request/response schemas
│   ├── transcript.py      # Transcript extraction + disk-persisted 30-day cache
│   ├── summarizer.py      # Chunking & multi-level summarization (Gemini)
│   ├── utils.py           # URL parsing, text cleaning, chunking
│   └── __init__.py
├── frontend/
│   ├── index.html         # Single-page UI
│   ├── style.css          # Dark theme with per-granularity styling
│   └── app.js             # SSE streaming, search history, markdown rendering
├── .cache/                # Disk-persisted transcript cache (gitignored)
├── requirements.txt       # Pinned dependencies
├── .env.example           # Environment variable template
├── .gitignore
├── CLAUDE.md              # Project intelligence for AI assistants
└── AGENT_INSTRUCTIONS.md  # Original build specification
```

---

## API Endpoints

### `GET /api/health`

Health check. Returns `{ "status": "ok" }`.

### `POST /api/transcript`

Extract transcript from a video URL (YouTube or Rumble).

```json
// Request
{ "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ" }

// Response
{
  "video_id": "dQw4w9WgXcQ",
  "title": "Video Title",
  "transcript": "Full transcript text...",
  "language": "en",
  "source": "captions",
  "duration_seconds": 212.0,
  "platform": "youtube"
}
```

### `POST /api/summarize`

Generate a streaming summary (SSE).

```json
// Request
{
  "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "granularity": "tldr",
  "output_language": "en",
  "custom_instruction": null
}
```

Response is a `text/event-stream` with these event types:

| Event | Meaning |
|-------|---------|
| *(no event)* | Summary text delta — append to output |
| `progress` | Status update (e.g., "Pass 1: chunk 3/5 done") |
| `metadata` | JSON with token counts, model, chunk info |
| `error` | Error message |
| `done` | Stream complete |

---

## Configuration

All settings via environment variables (see `.env.example`):

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GEMINI_API_KEY` | Yes | — | Your Google Gemini API key |
| `WHISPER_MODEL` | No | `base` | Whisper model size (`tiny`, `base`, `small`, `medium`, `large-v3`) |
| `MAX_CHUNK_WORDS` | No | `750` | Words per transcript chunk |
| `CHUNK_OVERLAP_WORDS` | No | `100` | Overlap between chunks for context continuity |
| `MAX_CONCURRENT_REQUESTS` | No | `3` | Max parallel LLM requests (semaphore) |
| `LOG_LEVEL` | No | `INFO` | Python logging level |
| `CACHE_TTL_HOURS` | No | `720` | How long to cache transcripts on disk (720h = 30 days) |

---

## Frontend Features

- **Dark theme** with per-granularity visual styling
- **Search history** — last 25 searches stored in localStorage with thumbnails, titles, and one-click re-run
- **Summary caching** — same video + same granularity within 30 days shows cached result instantly (zero API calls)
- **Live streaming** — summary appears token-by-token as it generates
- **Markdown rendering** via marked.js (headings, bullets, bold, code blocks)
- **Multi-platform** — supports YouTube and Rumble URLs
- **Responsive** — works on mobile
- **No build step** — served directly by FastAPI as static files

---

## Architecture Notes

- **No CORS issues** — frontend is served from the same FastAPI server via `StaticFiles` mount
- **Concurrency control** — `asyncio.Semaphore` limits parallel Gemini API calls to prevent rate limiting
- **Rate pacing** — 13s delay between API calls to stay within Gemini free tier (5 RPM)
- **Structured errors** — all errors return JSON with `error_code` and `message` fields
- **Fail-fast config** — missing `GEMINI_API_KEY` exits immediately with a clear message
- **Sandwich prompting** — format instructions placed before AND after content to enforce output structure
- **Disk-persisted cache** — transcript cache survives server restarts, stored in `.cache/transcripts.json`
