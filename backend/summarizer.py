"""Chunking and multi-granularity summarization with streaming support."""

import logging
import time
from collections.abc import AsyncGenerator

from google import genai
from google.genai import types

from backend.config import (
    CHUNK_OVERLAP_WORDS,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    MAX_CHUNK_WORDS,
    MAX_RETRIES,
)
from backend.models import GranularityLevel, SummaryResponse
from backend.utils import chunk_text

logger = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)


# --- Prompt templates per granularity ---

GRANULARITY_PROMPTS: dict[str, str] = {
    "one_liner": (
        "Produce exactly ONE sentence that captures the core idea of this content.\n\n"
        "Rules:\n"
        "- Output a single sentence, period at the end, nothing else\n"
        "- No bullet points, no headings, no preamble like 'Here is...' or 'This video...'\n"
        "- Do not start with 'The video' or 'This transcript'\n"
        "- Just the sentence, by itself\n\n"
        "Example output format:\n"
        "Compound interest is the most powerful force in personal finance because it turns "
        "small, consistent investments into exponential wealth over decades."
    ),
    "tldr": (
        "Produce exactly 3 to 5 bullet points capturing the key takeaways.\n\n"
        "Rules:\n"
        "- Use markdown bullet format (- )\n"
        "- Bold the key concept at the start of each bullet, then explain it\n"
        "- Each bullet must be a complete, standalone insight (1-2 sentences)\n"
        "- Each bullet MUST be on its own line, separated by a blank line from the next bullet\n"
        "- No introductory text, no concluding text, no headings\n"
        "- Output ONLY the bullet points, nothing before or after them\n\n"
        "Example output format:\n\n"
        "- **Compound interest**: Small, consistent investments grow exponentially over time, "
        "making early starts disproportionately valuable.\n\n"
        "- **Dollar-cost averaging**: Investing a fixed amount regularly smooths out market "
        "volatility and removes emotional decision-making.\n\n"
        "- **Emergency fund first**: Before investing, build 3-6 months of expenses in a "
        "liquid account to avoid forced selling during downturns."
    ),
    "short": (
        "Write exactly 2 paragraphs summarizing this content.\n\n"
        "Rules:\n"
        "- Paragraph 1: State the main topic/thesis and the most important point\n"
        "- Paragraph 2: Cover secondary insights, conclusions, or implications\n"
        "- Each paragraph should be 3-5 sentences\n"
        "- Separate the two paragraphs with a blank line\n"
        "- No headings, no bullet points, no bold text\n"
        "- Do not start with 'This video' or 'The speaker' — write as a standalone summary"
    ),
    "detailed": (
        "Write a structured summary using the exact markdown format below.\n\n"
        "Rules:\n"
        "- Start with a 1-2 sentence overview paragraph (no heading above it)\n"
        "- Then use ## headings for each major section\n"
        "- Under each heading, write 1-2 paragraphs of prose\n"
        "- Use sub-bullets (- ) sparingly for lists of specific items, examples, or data points\n"
        "- End with a ## Key Takeaways section containing 2-4 bullet points\n"
        "- Use **bold** for important terms on first mention\n\n"
        "Example output skeleton:\n\n"
        "A brief overview sentence about the content.\n\n"
        "## Main Argument\n\n"
        "Prose paragraph explaining the core thesis...\n\n"
        "## Supporting Evidence\n\n"
        "Prose paragraph with details...\n\n"
        "- Specific example or data point\n"
        "- Another specific point\n\n"
        "## Key Takeaways\n\n"
        "- First takeaway\n"
        "- Second takeaway"
    ),
    "chapters": (
        "Break this content into logical chapters based on topic shifts.\n\n"
        "Rules:\n"
        "- Each chapter gets a ## heading with a descriptive title\n"
        "- Immediately below the heading, put the timestamp range in italics on its own line "
        "(if timestamps are available; otherwise omit)\n"
        "- Then write exactly 2-3 sentences summarizing that chapter\n"
        "- Separate each chapter with a horizontal rule (---)\n"
        "- Identify 4-8 chapters based on natural topic transitions\n"
        "- Do NOT add any introduction or conclusion outside the chapters\n\n"
        "Example output skeleton:\n\n"
        "## Introduction and Background\n\n"
        "*0:00 – 3:45*\n\n"
        "The speaker opens by describing... They explain that... This sets up the main discussion.\n\n"
        "---\n\n"
        "## The Core Framework\n\n"
        "*3:45 – 12:20*\n\n"
        "The main concept introduced is... This works by... The key insight is that..."
    ),
}


def _build_system_prompt(
    granularity: GranularityLevel,
    transcript_language: str,
    output_language: str | None,
    custom_instruction: str | None,
) -> str:
    """Build the system prompt for summarization."""
    parts = [
        "You are a precise summarization assistant. You summarize video transcripts.",
        f"The original transcript is in: {transcript_language}.",
    ]

    if output_language:
        parts.append(f"Respond in: {output_language}.")
    else:
        parts.append(f"Respond in the same language as the transcript ({transcript_language}).")

    if granularity == "custom" and custom_instruction:
        parts.append(f"Follow this specific instruction: {custom_instruction}")
    elif granularity in GRANULARITY_PROMPTS:
        parts.append(GRANULARITY_PROMPTS[granularity])

    parts.append("Output only the summary — no meta-commentary about the summarization process.")

    return "\n\n".join(parts)


def _format_reminder(granularity: GranularityLevel) -> str:
    """Build an explicit format reminder for Pass 2 user messages (sandwich pattern)."""
    reminders: dict[str, str] = {
        "one_liner": (
            "OUTPUT FORMAT (STRICT): Respond with exactly ONE sentence. "
            "No preamble, no bullets, no headings. Just one sentence with a period at the end."
        ),
        "tldr": (
            "OUTPUT FORMAT (STRICT): Respond with exactly 3-5 markdown bullet points.\n"
            "Format each as: - **Key Concept**: Explanation sentence.\n"
            "Output NOTHING before or after the bullets. No introduction, no conclusion."
        ),
        "short": (
            "OUTPUT FORMAT (STRICT): Respond with exactly 2 paragraphs separated by a blank line.\n"
            "No headings, no bullets, no bold. Just two prose paragraphs of 3-5 sentences each."
        ),
        "detailed": (
            "OUTPUT FORMAT (STRICT): Start with 1-2 overview sentences (no heading), then use ## headings "
            "for each section. Include prose under each heading with occasional sub-bullets. "
            "End with a ## Key Takeaways section with 2-4 bullet points."
        ),
        "chapters": (
            "OUTPUT FORMAT (STRICT): Output a sequence of chapters, each formatted as:\n"
            "## Chapter Title\n\n*timestamp range*\n\n2-3 sentence summary.\n\n---\n\n"
            "Separate chapters with ---. No introduction or conclusion outside chapters. 4-8 chapters."
        ),
    }
    return reminders.get(granularity, "Follow the output format specified in your instructions exactly.")


# Pacing for free tier (5 RPM) — minimum seconds between API calls
_FREE_TIER_DELAY: float = 13.0
_last_request_time: float = 0.0


def _pace_request() -> None:
    """Wait if needed to stay under the free tier rate limit."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < _FREE_TIER_DELAY and _last_request_time > 0:
        wait = _FREE_TIER_DELAY - elapsed
        logger.info(f"Rate pacing: waiting {wait:.1f}s before next API call")
        time.sleep(wait)
    _last_request_time = time.time()


def _summarize_sync(system_prompt: str, user_content: str) -> tuple[str, int, int]:
    """Make a non-streaming Gemini API call. Returns (text, input_tokens, output_tokens)."""
    max_retries = 5
    for attempt in range(max_retries):
        try:
            _pace_request()
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=4096,
                ),
            )
            text = response.text or ""
            input_tokens = response.usage_metadata.prompt_token_count if response.usage_metadata else 0
            output_tokens = response.usage_metadata.candidates_token_count if response.usage_metadata else 0
            return text, input_tokens, output_tokens
        except Exception as e:
            error_msg = str(e).lower()
            if "429" in error_msg or "resource_exhausted" in error_msg or "rate" in error_msg:
                if attempt < max_retries - 1:
                    wait = 15 * (attempt + 1)
                    logger.warning(f"Rate limited. Retrying in {wait}s (attempt {attempt + 1}/{max_retries})")
                    time.sleep(wait)
                else:
                    raise
            else:
                raise
    raise RuntimeError("Unreachable")


async def summarize(
    transcript: str,
    granularity: GranularityLevel,
    transcript_language: str,
    output_language: str | None = None,
    custom_instruction: str | None = None,
) -> SummaryResponse:
    """Generate a non-streaming summary. Used for short content or Pass 1 chunk summaries."""
    chunks = chunk_text(transcript, MAX_CHUNK_WORDS, CHUNK_OVERLAP_WORDS)
    chunk_count = len(chunks)

    logger.info(f"Summarizing: granularity={granularity}, chunks={chunk_count}")

    system_prompt = _build_system_prompt(granularity, transcript_language, output_language, custom_instruction)
    total_input_tokens = 0
    total_output_tokens = 0

    if chunk_count == 1:
        # Single chunk — direct summarization (sandwich pattern)
        user_msg = (
            f"{_format_reminder(granularity)}\n\n"
            f"Transcript:\n\n{chunks[0]}\n\n"
            f"{_format_reminder(granularity)}"
        )
        summary, inp, out = _summarize_sync(system_prompt, user_msg)
        total_input_tokens += inp
        total_output_tokens += out
    else:
        # Multi-chunk — two-pass
        # Pass 1: summarize each chunk
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            user_msg = f"This is chunk {i + 1} of {chunk_count} from a video transcript.\n\nTranscript chunk:\n\n{chunk}"
            pass1_system = (
                "You are a precise summarization assistant. "
                "Summarize this transcript chunk, preserving all key information and details. "
                "This summary will be combined with summaries of other chunks for a final synthesis."
            )
            text, inp, out = _summarize_sync(pass1_system, user_msg)
            chunk_summaries.append(text)
            total_input_tokens += inp
            total_output_tokens += out
            logger.info(f"Pass 1: chunk {i + 1}/{chunk_count} done")

        # Pass 2: synthesize chunk summaries into final output
        combined = "\n\n---\n\n".join(
            f"[Section {i + 1}]\n{s}" for i, s in enumerate(chunk_summaries)
        )
        user_msg = (
            f"{_format_reminder(granularity)}\n\n"
            f"Below are summaries of {chunk_count} sequential sections from a video transcript. "
            f"Synthesize them into a single cohesive summary.\n\n{combined}"
            f"\n\n{_format_reminder(granularity)}"
        )
        summary, inp, out = _summarize_sync(system_prompt, user_msg)
        total_input_tokens += inp
        total_output_tokens += out

    return SummaryResponse(
        granularity=granularity,
        summary=summary,
        chunk_count=chunk_count,
        model=GEMINI_MODEL,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
    )


async def summarize_stream(
    transcript: str,
    granularity: GranularityLevel,
    transcript_language: str,
    output_language: str | None = None,
    custom_instruction: str | None = None,
) -> AsyncGenerator[str, None]:
    """Generate a streaming summary via SSE.

    For multi-chunk transcripts, Pass 1 runs non-streaming, then Pass 2 streams.
    Yields text deltas as they arrive.
    """
    chunks = chunk_text(transcript, MAX_CHUNK_WORDS, CHUNK_OVERLAP_WORDS)
    chunk_count = len(chunks)

    logger.info(f"Streaming summarize: granularity={granularity}, chunks={chunk_count}")

    system_prompt = _build_system_prompt(granularity, transcript_language, output_language, custom_instruction)

    if chunk_count == 1:
        user_content = (
            f"{_format_reminder(granularity)}\n\n"
            f"Transcript:\n\n{chunks[0]}\n\n"
            f"{_format_reminder(granularity)}"
        )
    else:
        # Pass 1: non-streaming chunk summaries
        chunk_summaries = []
        for i, chunk in enumerate(chunks):
            user_msg = f"This is chunk {i + 1} of {chunk_count} from a video transcript.\n\nTranscript chunk:\n\n{chunk}"
            pass1_system = (
                "You are a precise summarization assistant. "
                "Summarize this transcript chunk, preserving all key information and details. "
                "This summary will be combined with summaries of other chunks for a final synthesis."
            )
            text, _, _ = _summarize_sync(pass1_system, user_msg)
            chunk_summaries.append(text)
            # Yield a progress event
            yield f"event: progress\ndata: Pass 1: chunk {i + 1}/{chunk_count} done\n\n"

        combined = "\n\n---\n\n".join(
            f"[Section {i + 1}]\n{s}" for i, s in enumerate(chunk_summaries)
        )
        user_content = (
            f"{_format_reminder(granularity)}\n\n"
            f"Below are summaries of {chunk_count} sequential sections from a video transcript. "
            f"Synthesize them into a single cohesive summary.\n\n{combined}"
            f"\n\n{_format_reminder(granularity)}"
        )

    # Stream the final summarization (Pass 2 or direct)
    total_input_tokens = 0
    total_output_tokens = 0

    for attempt in range(5):
        try:
            _pace_request()
            response_stream = client.models.generate_content_stream(
                model=GEMINI_MODEL,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=4096,
                ),
            )

            for chunk in response_stream:
                text = chunk.text or ""
                if text:
                    # SSE spec: multi-line data uses multiple "data:" lines,
                    # joined by \n on client side
                    for line in text.split("\n"):
                        yield f"data: {line}\n"
                    yield "\n"

                # Accumulate token counts from final chunk
                if chunk.usage_metadata:
                    if chunk.usage_metadata.prompt_token_count:
                        total_input_tokens = chunk.usage_metadata.prompt_token_count
                    if chunk.usage_metadata.candidates_token_count:
                        total_output_tokens = chunk.usage_metadata.candidates_token_count

            break
        except Exception as e:
            error_msg = str(e).lower()
            if "429" in error_msg or "resource_exhausted" in error_msg or "rate" in error_msg:
                if attempt < 4:
                    wait = 15 * (attempt + 1)
                    logger.warning(f"Rate limited during stream. Retrying in {wait}s")
                    time.sleep(wait)
                    yield f"event: progress\ndata: Rate limited, retrying in {wait}s...\n\n"
                else:
                    yield f"event: error\ndata: Rate limit exceeded after 5 retries\n\n"
                    return
            else:
                yield f"event: error\ndata: {e}\n\n"
                return

    # Final metadata event
    import json
    metadata = json.dumps({
        "granularity": granularity,
        "chunk_count": chunk_count,
        "model": GEMINI_MODEL,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
    })
    yield f"event: metadata\ndata: {metadata}\n\n"
    yield f"event: done\ndata: complete\n\n"
