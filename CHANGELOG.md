# CHANGELOG

## 2026-03-12 — Non-interrupting request queue + deployment setup

### Context
Two changes in one release: (1) frontend card-based queue system so requests don't interrupt each other, and (2) deployment configuration for Render free tier.

### What changed
- **Card queue system (frontend only):** Replaced singleton output area with stacked result cards. Each submission gets its own card with independent status, metadata, and summary. Only one API stream runs at a time; additional requests queue. Cached results appear instantly without interrupting active streams. Cards are dismissable (AbortController) and collapsible.
- **Deployment files:** Added `Dockerfile` (Python 3.12-slim + FFmpeg), `.dockerignore`, and `render.yaml` (Render blueprint with free plan config). Uses `tiny` Whisper model and `MAX_CONCURRENT_REQUESTS=1` to fit within 512MB RAM.
- **GitHub:** Pushed to `augustok87/youtube-transcript-summarizer` as new remote

### Impact
- Users can submit multiple videos without losing in-progress work
- App is deployable to Render free tier with zero configuration beyond setting `GEMINI_API_KEY`
- Cold starts (~30s) after 15min inactivity on free tier; ephemeral disk means backend cache resets on restart (frontend localStorage cache still works)

---

## 2026-03-10 — Switch from Anthropic Claude to Google Gemini + 30-day disk cache

### Context
Anthropic API credits were exhausted. Switched to Google Gemini API which offers a free tier (5 RPM, 250K TPM). Also extended the cache TTL from 24 hours to 30 days and persisted the backend transcript cache to disk so it survives server restarts.

### What changed
- **LLM provider:** Replaced `anthropic` SDK with `google-genai` SDK. Model: `gemini-2.5-flash`
- **Config:** `ANTHROPIC_API_KEY` → `GEMINI_API_KEY`, `CLAUDE_MODEL` → `GEMINI_MODEL`
- **Rate pacing:** Added 13s delay between API calls (`_pace_request()`) to stay under the free tier's 5 RPM limit. Retry logic increased to 5 attempts with 15s increments
- **Backend cache:** Now persists to `.cache/transcripts.json` (gitignored). Loaded into memory on startup, written to disk on each new entry. Expired entries are pruned on load
- **Cache TTL:** Backend default raised from 24h to 720h (30 days). Frontend localStorage TTL raised from 24h to 30 days
- **Dependencies:** Removed `anthropic==0.52.0`, added `google-genai==1.66.0`

### Impact
- Long videos (15+ chunks) take ~3-4 minutes on the free tier due to rate pacing. Short videos are fast
- Cached transcripts now survive `uvicorn` restarts — no re-extraction needed
- Disk usage is minimal (~5-50 KB per cached transcript)

### Future considerations
- If billing is enabled on the Google Cloud project, `_FREE_TIER_DELAY` can be reduced to 0 for instant throughput
- The disk cache could be replaced with SQLite if the number of cached entries grows very large

---

## 2026-03-10 — Rumble.com video support

### Context
Extended the app to support Rumble.com video URLs in addition to YouTube.

### What changed
- **URL parsing:** `parse_video_url()` in `utils.py` now returns `(platform, video_id)` for both YouTube and Rumble
- **Platform routing:** YouTube tries captions first → Whisper fallback; Rumble goes straight to Whisper
- **yt-dlp format fix:** Format selector changed to `bestaudio[ext!=tar]/best[ext!=tar]/bestaudio/best` to avoid Rumble's `.tar` format files
- **Models:** Added `platform` field to `TranscriptResponse`
- **Frontend:** Added Rumble URL recognition for caching, SVG placeholder thumbnails for non-YouTube history entries

### Impact
- Rumble videos require Whisper transcription (slower than YouTube captions)
- Cache key is now `platform:video_id` to avoid collisions between platforms

---

## 2026-03-06 — Initial build

### What was built
- Full-stack video transcript summarizer with FastAPI backend and plain HTML/JS frontend
- YouTube transcript extraction via `youtube-transcript-api` with `faster-whisper` fallback
- Six summarization granularity levels with SSE streaming
- Two-pass chunking pipeline for long videos
- Search history with localStorage persistence
- Frontend summary caching for instant replays
