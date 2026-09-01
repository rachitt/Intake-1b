"""Vision extraction via Gemini.

Only the pages the locator nominated are rasterised and sent, which is what keeps this
affordable: the reference protocols total roughly 370 pages but only about a dozen carry a
schedule. All pages of one span go in a single request so the model can reconcile a
continuation header against the page before it, and so a footnote spilling across a page
break is visible to it as one document rather than two.
"""

from __future__ import annotations

import os
import time

from ..pdfdoc import PdfDoc
from ..locate.spans import TableSpan
from .prompt import SYSTEM_PROMPT, VSchedule, build_user_prompt

# 3.5-flash is the default because it answered reliably throughout development, while
# 3.7-flash returned 503 "high demand" often enough to stall a run. Override with
# SOA_GEMINI_MODEL; the fallback chain below is tried in order if the choice is refused.
DEFAULT_MODEL = "gemini-3.5-flash"
DEFAULT_DPI = 200

# Fallbacks tried in order if the configured model is unavailable to this key.
_MODEL_FALLBACKS = ["gemini-3.5-flash", "gemini-3-flash-preview", "gemini-2.5-flash"]


class VisionUnavailable(RuntimeError):
    """Raised when no API key is configured. The pipeline degrades rather than dies."""


def _client():
    try:
        from google import genai
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise VisionUnavailable(f"google-genai is not installed: {exc}") from exc

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise VisionUnavailable(
            "No GEMINI_API_KEY (or GOOGLE_API_KEY) in the environment. "
            "The geometric engine will run alone and the output will say so."
        )
    return genai.Client(api_key=key)


def model_name() -> str:
    return os.environ.get("SOA_GEMINI_MODEL", DEFAULT_MODEL)


def _thinking_budget() -> int:
    try:
        return int(os.environ.get("SOA_THINKING_BUDGET", "4096"))
    except ValueError:
        return 4096


def render_dpi() -> int:
    try:
        return int(os.environ.get("SOA_RENDER_DPI", DEFAULT_DPI))
    except ValueError:
        return DEFAULT_DPI


def extract_span(
    doc: PdfDoc,
    span: TableSpan,
    text_layer_trustworthy: bool = True,
    max_retries: int = 3,
) -> tuple[VSchedule, dict]:
    """Transcribe one span. Returns the model's schedule plus run metadata.

    Both the table pages and the footnote-candidate pages are sent. The extra page is what
    lets the model find a footnote block that continues past the table with no heading and
    no marker -- the failure the brief warns is easy to miss unless you go looking.
    """
    from google.genai import types

    client = _client()
    dpi = render_dpi()

    pages = sorted(set(span.pages) | set(span.footnote_pages))
    pages = [p for p in pages if 1 <= p <= len(doc)]

    parts: list = []
    text_chunks: list[str] = []
    for page_no in pages:
        page = doc.page_no(page_no)
        parts.append(
            types.Part.from_bytes(data=page.render_png(dpi=dpi), mime_type="image/png")
        )
        role = "table" if page_no in span.pages else "footnote/continuation candidate"
        text_chunks.append(f"[page {page_no} - {role}]\n{page.text}")

    user_prompt = build_user_prompt(
        heading_hint=span.heading,
        page_numbers=[str(p) for p in pages],
        text_layer="\n\n".join(text_chunks),
        text_layer_trustworthy=text_layer_trustworthy,
    )
    parts.append(types.Part.from_text(text=user_prompt))

    config = types.GenerateContentConfig(
        system_instruction=SYSTEM_PROMPT,
        response_mime_type="application/json",
        response_schema=VSchedule,
        temperature=0.0,
        max_output_tokens=64000,
        # Transcription is a careful-reading task, not a reasoning one, and an unbounded
        # thinking budget roughly doubled wall-clock time without changing the output in
        # side-by-side runs. A modest budget keeps the model deliberate about row counts
        # while staying inside a tolerable response time.
        thinking_config=types.ThinkingConfig(thinking_budget=_thinking_budget()),
        # Table cells are small; the default downscale loses superscript markers.
        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
    )

    candidates = [model_name()] + [m for m in _MODEL_FALLBACKS if m != model_name()]
    last_error: Exception | None = None

    for model in candidates:
        for attempt in range(max_retries):
            try:
                started = time.time()
                response = client.models.generate_content(
                    model=model,
                    contents=[types.Content(role="user", parts=parts)],
                    config=config,
                )
                parsed = response.parsed
                if parsed is None:
                    raise ValueError("model returned no parsable structured output")

                usage = getattr(response, "usage_metadata", None)
                meta = {
                    "model": model,
                    "pages_sent": len(pages),
                    "dpi": dpi,
                    "seconds": round(time.time() - started, 2),
                    "input_tokens": getattr(usage, "prompt_token_count", None),
                    "output_tokens": getattr(usage, "candidates_token_count", None),
                }
                return parsed, meta

            except Exception as exc:  # noqa: BLE001 - surfaced to the caller as a warning
                last_error = exc
                message = str(exc).lower()
                # A missing model is permanent; an overloaded one is worth one retry
                # before moving on rather than burning the whole backoff budget.
                if "not found" in message or "not supported" in message:
                    break
                if ("503" in message or "unavailable" in message) and attempt >= 1:
                    break
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

    raise RuntimeError(f"vision extraction failed: {last_error}")
