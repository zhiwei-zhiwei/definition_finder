from openai import OpenAI

from app.config import settings


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.OPENAI_API_KEY or None)
    return _client


def embed_batch(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    client = _get_client()
    resp = client.embeddings.create(model=settings.EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]
