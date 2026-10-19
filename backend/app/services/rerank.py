"""Cross-encoder reranking of retrieved child chunks (settings.RERANK_MODEL; empty = off).

score() has the shape of Hugging Face Text Embeddings Inference's POST /rerank
({query, texts} -> one score per text), so serving the model as a TEI pod later only
swaps this function's body for an HTTP call."""
from functools import lru_cache


@lru_cache(maxsize=2)
def _load(name: str):
    import torch  # lazy: only paid when reranking is on
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = "cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu"
    # fp16 off-CPU: in fp32, bge-reranker-v2-m3 took 14 GB of MPS memory on a 16 GB Mac and
    # swapped (7 s -> 25 s per query); fp16 held at ~6 s and half the memory
    dtype = torch.float32 if device == "cpu" else torch.float16
    model = AutoModelForSequenceClassification.from_pretrained(name, dtype=dtype).to(device).eval()
    return AutoTokenizer.from_pretrained(name), model, device


def score(model_name: str, query: str, texts: list[str]) -> list[float]:
    """Relevance in [0, 1] for each (query, text) pair, in input order."""
    import torch

    tok, model, device = _load(model_name)
    # ponytail: one batch for all candidates (50 by default); split it if RERANK_CANDIDATES grows a lot
    enc = tok([query] * len(texts), texts, padding=True, truncation=True, max_length=512, return_tensors="pt").to(device)
    with torch.inference_mode():
        return torch.sigmoid(model(**enc).logits.view(-1).float()).tolist()
