# CLAUDE.md — Project Intelligence for Video Transcript Summarizer

## Project Overview
A full-stack application that accepts a video URL (YouTube or Rumble), extracts the full transcript (captions or Whisper fallback), and generates summaries at six granularity levels. Python backend (FastAPI) with a plain HTML/JS frontend served statically.

## Tech Stack
- **Language:** Python 3.11+
- **AI:** Google Gemini API (`gemini-2.5-flash`) with streaming
- **Transcript:** `youtube-transcript-api` (YouTube captions), `yt-dlp` + `faster-whisper` (fallback / Rumble)
- **Platforms:** YouTube (captions + Whisper), Rumble (Whisper only)
- **API:** FastAPI with SSE streaming for real-time summary delivery
- **Frontend:** Plain HTML/CSS/JS (no build step), served by FastAPI as static files
- **Validation:** Pydantic v2 for request/response models
- **Config:** `.env` with fail-fast validation at startup via `config.py`

## Architecture

### Request Flow
1. User submits video URL (YouTube or Rumble) + granularity level
2. Platform detected via `parse_video_url()` → `(platform, video_id)`
3. Backend extracts transcript: YouTube tries captions first then Whisper; Rumble uses Whisper directly
3. Long transcripts are chunked (~750 words/chunk with 100-word overlap)
4. Two-pass summarization: chunk summaries → final synthesis
5. Final synthesis streams via SSE to the frontend

### File Structure
```
backend/
  main.py            — FastAPI app, static serving, SSE endpoints, concurrency control
  transcript.py      — Multi-platform transcript extraction + Whisper fallback + disk-persisted cache
  summarizer.py      — Chunking, two-pass summarization, streaming
  models.py          — Pydantic v2 request/response schemas
  config.py          — Env var loading, validation, constants
  utils.py           — URL parsing (multi-platform), text cleaning, chunking helpers
frontend/
  index.html         — Single-page UI
  style.css          — Minimal styling
  app.js             — SSE handling, Markdown rendering, search history, summary caching
```

### API Endpoints
- `POST /api/transcript` — Extract transcript from video URL (YouTube, Rumble)
- `POST /api/summarize` — Generate summary (returns SSE stream)
- `GET /api/health` — Health check

### Caching (Two Layers)
- **Backend:** Disk-persisted transcript cache keyed by `platform:video_id` with configurable TTL (default 30 days). Stored in `.cache/transcripts.json`, survives server restarts. Loaded into memory on startup, written to disk on each new entry.
- **Frontend:** Summary cache in localStorage keyed by `video_id + granularity` with 30-day TTL. Same video + same style = instant display, zero API calls.

## Coding Principles

### 1. Clarity Over Cleverness
- Write code that reads like well-edited prose. A junior dev should understand every function.
- Name things for what they DO: `extract_captions()` > `get_data()`.
- One function, one job. If you need "and" to describe it, split it.

### 2. Minimal Viable Abstraction
- Don't abstract until you have 3+ concrete duplications.
- Prefer flat, linear code over nested abstractions.
- Configuration objects over long parameter lists.

### 3. Type Safety as Documentation
- Type hints on all function signatures — no exceptions.
- Pydantic models with `extra="forbid"` for all API boundaries.
- Never use `Any`. Use `object` + narrowing when dealing with external data.

### 4. Error Handling Philosophy
- Validate at boundaries: user input, API responses, external tool outputs.
- Internal code trusts the type system — no defensive coding within modules.
- Errors should be informative: what failed, what was expected, what to try.
- Return structured JSON errors with specific `error_code` values, never raw tracebacks.

### 5. Fail Loud, Recover Gracefully
- Missing API keys? Fail immediately at startup with a clear message.
- Transcript extraction fails? Try Whisper fallback before giving up.
- Gemini API returns 429? Rate pacing (13s between calls) + exponential backoff, up to 5 retries.
- Too many concurrent requests? HTTP 503 with `Retry-After`.

### 6. Observability
- Log transcript source decisions (captions vs. Whisper) at `INFO`.
- Log chunk counts and token usage per summarization at `INFO`.
- Log all API errors with status codes at `ERROR`.
- Never log API keys or full transcript text.

## Code Style

### Python Conventions
- Use `async/await` throughout FastAPI handlers — no sync blocking.
- Type hints on all functions. Docstrings on all public functions.
- f-strings over `.format()` or `%` formatting.
- Use `pathlib.Path` over `os.path` for file operations.
- Prefer `httpx` (async) over `requests` if HTTP calls are needed.

### File Conventions
- One module = one responsibility.
- Imports: stdlib → third-party → local, separated by blank lines.
- No circular imports — dependency flows downward: `main` → `summarizer`/`transcript` → `config`/`utils`.

### Naming
- Files: `snake_case.py` — always.
- Classes: `PascalCase`. Models suffixed with `Request`/`Response`.
- Functions/Variables: `snake_case`. Booleans start with `is_`/`has_`/`should_`.
- Constants: `UPPER_SNAKE_CASE` in `config.py` only.

## Working with This Codebase
```bash
# Install dependencies
pip install -r requirements.txt

# Copy env template and fill in API key
cp .env.example .env

# Run the server
uvicorn backend.main:app --reload

# Frontend is served at http://localhost:8000/
```

## API Key Management
- All secrets in `.env` (never committed).
- `.env.example` shows required keys with placeholder values.
- `config.py` validates all required env vars at import time — fail fast.

## Testing Strategy
- Manual testing with diverse videos during development.
- Test cases: short YouTube video, long video (>1h), non-English, captions disabled, invalid URL, Rumble video.
- Each module testable in isolation via `python -m backend.transcript` etc.
- Evaluate summaries on: accuracy, appropriate length for granularity, language match.

## Common Pitfalls
1. **CORS issues:** Avoided by serving frontend statically from FastAPI — same origin.
2. **Whisper memory:** `faster-whisper` is lighter than OpenAI's `whisper`, but still chunk audio for >2h videos.
3. **Token limits:** Chunk at word boundaries, not character boundaries. Use overlap to preserve context.
4. **Streaming errors:** If Gemini errors mid-stream, send an SSE error event so the frontend can display it.
5. **Cleanup:** Always clean up downloaded audio files after Whisper transcription (`try/finally`).
6. **Concurrency:** Semaphore in `main.py` prevents overwhelming the Gemini API with parallel requests.

## Documentation Requirements
Every significant change to the codebase must be documented in CHANGELOG.md with:
- **Context:** Why the change was made
- **What changed:** Concrete description of modifications
- **Impact:** What this affects downstream
- **Future considerations:** Known limitations or planned improvements
