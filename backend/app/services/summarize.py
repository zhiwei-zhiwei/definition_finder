from collections.abc import Iterator

from openai import OpenAI

from app.config import settings
from app.services.telemetry import record_tokens


SYSTEM_PROMPT = """You are a serious document analyst. You will be
given (1) a user's question about a term or definition, and (2) relevant
excerpts from the source document. Produce a concise, well-structured
summary that directly answers the question. If the document does not
contain a definition, say so plainly — do not fabricate one. Cite the
page number(s) in parentheses where the information appears. Excerpts mark
page breaks with [page N]: cite the page of the nearest [page N] marker
before the information, not just the excerpt's first page. Put the citation
right after each fact, not once for a whole paragraph or list.

Only report figures the excerpts state. Do not compute a total, growth
rate or percentage from partial figures (e.g. adding up some segments); if
the requested figure is not stated, say so.

When the excerpts do not contain the answer, say so without citing pages:
a page list for what was not found is not a citation.

End with a line formatted exactly like:
**Key Takeaway:** <one sentence>"""


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY or None, timeout=settings.OPENAI_TIMEOUT)
    return _client


def _format_context(query: str, parent_texts: list[str], pages: list[int | None]) -> str:
    blocks = []
    for i, (txt, page) in enumerate(zip(parent_texts, pages), start=1):
        # Marked text carries its own [page N] markers; a "(page N)" header pulled
        # citations to the excerpt's first page even for facts pages later.
        page_label = f"(page {page})" if page and not txt.startswith("[page ") else ""
        blocks.append(f"### Excerpt {i} {page_label}\n{txt}")
    ctx = "\n\n".join(blocks) if blocks else "(no excerpts retrieved)"
    return f"USER QUESTION:\n{query}\n\nSOURCE EXCERPTS:\n{ctx}"


def summarize_stream(query: str, parent_texts: list[str], pages: list[int | None]) -> Iterator[str]:
    client = _get_client()
    # `with`: closing the stream early (a cancelled run) drops the connection, so OpenAI stops generating.
    with client.chat.completions.create(
        model=settings.CHAT_MODEL,
        stream=True,
        stream_options={"include_usage": True},  # usage comes in a last, choice-less chunk
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _format_context(query, parent_texts, pages)},
        ],
        temperature=0.2,
    ) as resp:
        for chunk in resp:
            record_tokens("chat", settings.CHAT_MODEL, chunk.usage)
            try:
                delta = chunk.choices[0].delta.content
            except (IndexError, AttributeError):
                delta = None
            if delta:
                yield delta
