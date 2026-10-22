from openai import OpenAI

from app.config import settings


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY or None, timeout=settings.OPENAI_TIMEOUT)
    return _client


def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = _get_client()
    kwargs = {"model": settings.EMBED_MODEL, "input": texts}
    if settings.EMBED_DIM:
        kwargs["dimensions"] = settings.EMBED_DIM
    resp = client.embeddings.create(**kwargs)
    return [d.embedding for d in resp.data]
