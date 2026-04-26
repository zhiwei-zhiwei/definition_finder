import chromadb

from app.config import settings


_client = None


def get_client():
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=str(settings.chroma_path))
    return _client


def collection_for(doc_id: str):
    name = f"doc_{doc_id.replace('-', '')}"
    return get_client().get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


def delete_collection(doc_id: str) -> None:
    name = f"doc_{doc_id.replace('-', '')}"
    try:
        get_client().delete_collection(name=name)
    except Exception:
        pass
