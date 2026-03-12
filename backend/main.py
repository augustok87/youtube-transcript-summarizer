"""FastAPI application — API endpoints, static file serving, concurrency control."""

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from backend.config import LOG_LEVEL, MAX_CONCURRENT_REQUESTS
from backend.models import (
    ErrorResponse,
    HealthResponse,
    SummarizeRequest,
    TranscriptRequest,
    TranscriptResponse,
)
from backend.transcript import TranscriptError, extract_transcript
from backend.summarizer import summarize_stream

# --- Logging setup ---
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# --- App ---
app = FastAPI(title="YouTube Transcript Summarizer", version="1.0.0")

# Concurrency limiter for LLM calls
_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)


# --- Error handlers ---
@app.exception_handler(TranscriptError)
async def transcript_error_handler(request, exc: TranscriptError):
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(error_code=exc.error_code, message=exc.message).model_dump(),
    )


@app.exception_handler(Exception)
async def generic_error_handler(request, exc: Exception):
    logger.error(f"Unhandled error: {type(exc).__name__}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(error_code="internal_error", message=str(exc)).model_dump(),
    )


# --- API Routes ---

@app.get("/api/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok")


@app.post("/api/transcript", response_model=TranscriptResponse)
async def get_transcript(req: TranscriptRequest):
    """Extract transcript from a YouTube URL."""
    return await extract_transcript(req.url)


@app.post("/api/summarize")
async def get_summary(req: SummarizeRequest):
    """Extract transcript and generate a streaming summary."""
    if req.granularity == "custom" and not req.custom_instruction:
        raise HTTPException(
            status_code=422,
            detail="custom_instruction is required when granularity is 'custom'",
        )

    # Check concurrency
    if _semaphore._value == 0:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error_code="server_busy",
                message="Too many concurrent requests. Please retry shortly.",
            ).model_dump(),
            headers={"Retry-After": "5"},
        )

    async def generate():
        async with _semaphore:
            transcript_resp = await extract_transcript(req.url)
            async for chunk in summarize_stream(
                transcript=transcript_resp.transcript,
                granularity=req.granularity,
                transcript_language=transcript_resp.language,
                output_language=req.output_language,
                custom_instruction=req.custom_instruction,
            ):
                yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")


# --- Static files (frontend) — must be last ---
_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
