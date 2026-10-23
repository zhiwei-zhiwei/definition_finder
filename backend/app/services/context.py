"""Contextual Retrieval — generate a short blurb situating each chunk inside its
document, then prepend it before embedding/indexing.

Adapted from Anthropic's "Contextual Embeddings" cookbook, with two deviations
chosen for this app:
  * the situating LLM is OpenAI (gpt-4.1-mini), so the whole stack stays on one
    vendor — OpenAI auto-caches the long, identical document prefix across a
    doc's chunks, which is what makes this cheap (no explicit cache_control);
  * context is generated per *parent* chunk, not per child. Children inherit
    their parent's blurb, cutting LLM calls ~8x to fit the parent-child design.
"""
import logging
from concurrent.futures import ThreadPoolExecutor

import tiktoken
from openai import OpenAI

from app.config import settings
from app.services.telemetry import record_tokens

log = logging.getLogger(__name__)

_ENC = tiktoken.get_encoding("cl100k_base")
# Each call ships the whole (cached) document as context, so a few parallel
# requests can burst past the model's tokens-per-minute limit. Keep concurrency
# low and let the SDK ride out 429s with backoff (it honors Retry-After) rather
# than degrading those parents to empty context.
_MAX_WORKERS = 3

_SYSTEM = (
    "You situate excerpts within their source document to improve search "
    "retrieval. Answer only with the succinct context and nothing else."
)

# Cookbook prompt. The <document> block is identical across a doc's chunks, so
# it forms the cacheable prefix; only the <chunk> block varies per call.
_PROMPT = """<document>
{doc}
</document>
Here is the chunk we want to situate within the whole document
<chunk>
{chunk}
</chunk>
Please give a short succinct context to situate this chunk within the overall \
document for the purposes of improving search retrieval of the chunk. Answer \
only with the succinct context and nothing else."""


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY or None, max_retries=8, timeout=60.0)
    return _client


def _truncate_doc(doc_text: str) -> str:
    toks = _ENC.encode(doc_text)
    if len(toks) <= settings.CONTEXT_MAX_DOC_TOKENS:
        return doc_text
    return _ENC.decode(toks[: settings.CONTEXT_MAX_DOC_TOKENS])


def generate_context(doc_text: str, chunk_text: str) -> str:
    """Return a 1-2 sentence blurb situating `chunk_text` in `doc_text`.

    Degrades to "" on any error so a single failed call never aborts a whole
    document's preparation — embedding remains the loud failure point if the
    OpenAI key is missing entirely.
    """
    try:
        resp = _get_client().chat.completions.create(
            model=settings.CONTEXT_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _PROMPT.format(doc=doc_text, chunk=chunk_text)},
            ],
            temperature=0.0,
            max_tokens=120,
        )
        record_tokens("chat", settings.CONTEXT_MODEL, resp.usage)
        return (resp.choices[0].message.content or "").strip()
    except Exception:
        log.warning("context generation failed for a chunk; using empty context", exc_info=True)
        return ""


def generate_parent_contexts(doc_text: str, parents: list[tuple[str, str]]) -> dict[str, str]:
    """Map parent_id -> situating context, generated concurrently.

    `parents` is a list of (parent_id, parent_text). Returns {} when the feature
    is disabled or there is nothing to situate against.
    """
    if not settings.CONTEXTUAL_EMBEDDINGS or not doc_text or not parents:
        return {}
    doc = _truncate_doc(doc_text)
    out: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        futures = {pool.submit(generate_context, doc, text): pid for pid, text in parents}
        for fut in futures:
            out[futures[fut]] = fut.result()
    return out
