"""Helpers for URL parsing, text cleaning, and common operations."""

import re
from urllib.parse import parse_qs, urlparse


def parse_video_url(url: str) -> tuple[str, str] | None:
    """Parse a video URL into (platform, video_id).

    Returns None if URL is not from a supported platform.
    Supports: YouTube, Rumble.
    """
    parsed = urlparse(url)
    hostname = parsed.hostname or ""

    # YouTube
    if hostname in ("youtu.be",):
        vid = parsed.path.lstrip("/")
        return ("youtube", vid) if vid else None
    if hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            qs = parse_qs(parsed.query)
            ids = qs.get("v")
            return ("youtube", ids[0]) if ids else None
        for prefix in ("/embed/", "/shorts/", "/v/"):
            if parsed.path.startswith(prefix):
                vid = parsed.path[len(prefix):].split("/")[0]
                return ("youtube", vid) if vid else None

    # Rumble
    if hostname in ("rumble.com", "www.rumble.com"):
        path = parsed.path.strip("/")
        if path:
            return ("rumble", path.replace(".html", "").replace("/", "_"))

    return None


def extract_video_id(url: str) -> str | None:
    """Extract video ID from a supported URL. Returns None if unsupported."""
    result = parse_video_url(url)
    return result[1] if result else None


def clean_transcript_text(text: str) -> str:
    """Remove common transcript artifacts and normalize whitespace."""
    # Remove speaker labels like "[Music]", "(applause)"
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_into_sentences(text: str) -> list[str]:
    """Split text into sentences, preserving sentence boundaries."""
    # Split on sentence-ending punctuation followed by space or end of string
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s.strip() for s in sentences if s.strip()]


def chunk_text(text: str, max_words: int, overlap_words: int) -> list[str]:
    """Split text into overlapping chunks at sentence boundaries.

    Each chunk targets ~max_words words, never splitting mid-sentence.
    Consecutive chunks overlap by ~overlap_words words for context continuity.
    """
    sentences = split_into_sentences(text)
    chunks: list[str] = []
    current_sentences: list[str] = []
    current_word_count = 0

    for sentence in sentences:
        sentence_words = len(sentence.split())

        if current_word_count + sentence_words > max_words and current_sentences:
            chunks.append(" ".join(current_sentences))

            # Build overlap from the end of the current chunk
            overlap_sentences: list[str] = []
            overlap_count = 0
            for s in reversed(current_sentences):
                s_words = len(s.split())
                if overlap_count + s_words > overlap_words:
                    break
                overlap_sentences.insert(0, s)
                overlap_count += s_words

            current_sentences = overlap_sentences
            current_word_count = overlap_count

        current_sentences.append(sentence)
        current_word_count += sentence_words

    if current_sentences:
        chunks.append(" ".join(current_sentences))

    return chunks
