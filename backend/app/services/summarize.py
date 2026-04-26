from collections.abc import Iterator

from openai import OpenAI

from app.config import settings


SYSTEM_PROMPT = """You are a serious document analyst. You will be
given (1) a user's question about a term or definition, and (2) relevant
excerpts from the source document. Produce a concise, well-structured
summary that directly answers the question. If the document does not
contain a definition, say so plainly — do not fabricate one. Cite the
page number(s) in parentheses where the information appears.

End with a line formatted exactly like:
**Key Takeaway:** <one sentence>"""


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY or None)
    return _client


def _format_context(query: str, parent_texts: list[str], pages: list[int]) -> str:
    blocks = []
    for i, (txt, page) in enumerate(zip(parent_texts, pages), start=1):
        page_label = f"(page {page})" if page else ""
        blocks.append(f"### Excerpt {i} {page_label}\n{txt}")
    ctx = "\n\n".join(blocks) if blocks else "(no excerpts retrieved)"
    return f"USER QUESTION:\n{query}\n\nSOURCE EXCERPTS:\n{ctx}"


def summarize_stream(query: str, parent_texts: list[str], pages: list[int]) -> Iterator[str]:
    client = _get_client()
    resp = client.chat.completions.create(
        model=settings.CHAT_MODEL,
        stream=True,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _format_context(query, parent_texts, pages)},
        ],
        temperature=0.2,
    )
    for chunk in resp:
        try:
            delta = chunk.choices[0].delta.content
        except (IndexError, AttributeError):
            delta = None
        if delta:
            yield delta
