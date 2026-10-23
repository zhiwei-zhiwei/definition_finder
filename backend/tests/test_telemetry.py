"""OpenTelemetry wiring: every API request gets a server span and http.server metrics, and
every OpenAI call records its token usage."""
from contextlib import nullcontext
from types import SimpleNamespace

from app.config import settings
from app.services import embed, summarize


def _route_points(otel_metrics, route):
    m = otel_metrics().get("http.server.request.duration")
    return [p for p in m.data.data_points if p.attributes.get("http.route") == route] if m else []


def test_request_gets_one_server_span_and_duration_metric(client, spans, otel_metrics):
    before = sum(p.count for p in _route_points(otel_metrics, "/documents/{doc_id}"))
    r = client.get("/documents/nope")
    assert r.status_code == 404
    (span,) = spans.get_finished_spans()  # no per-send/receive child spans
    assert span.name == "GET /documents/{doc_id}"
    assert span.attributes["http.route"] == "/documents/{doc_id}"
    assert span.attributes["http.response.status_code"] == 404
    after = _route_points(otel_metrics, "/documents/{doc_id}")
    assert sum(p.count for p in after) == before + 1
    assert {p.attributes["http.response.status_code"] for p in after} >= {404}


def test_health_checks_are_not_traced(client, spans):
    assert client.get("/health").status_code == 200
    assert spans.get_finished_spans() == ()


def _client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                           embeddings=SimpleNamespace(create=create))


def test_answer_tokens_are_recorded_from_the_streams_last_chunk(monkeypatch, metric_total):
    chunks = [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="Hi"))], usage=None),
              SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30))]
    sent = {}

    def create(**kw):
        sent.update(kw)
        return nullcontext(iter(chunks))  # the real Stream is a context manager

    monkeypatch.setattr(summarize, "_get_client", lambda: _client(create))
    chat = {"gen_ai.operation.name": "chat", "gen_ai.request.model": settings.CHAT_MODEL}
    before_in = metric_total("gen_ai.client.token.usage", {**chat, "gen_ai.token.type": "input"})[1]
    before_out = metric_total("gen_ai.client.token.usage", {**chat, "gen_ai.token.type": "output"})[1]
    assert list(summarize.summarize_stream("q", ["text"], [1])) == ["Hi"]
    assert sent["stream_options"] == {"include_usage": True}  # without it a stream reports no usage
    assert metric_total("gen_ai.client.token.usage", {**chat, "gen_ai.token.type": "input"})[1] == before_in + 120
    assert metric_total("gen_ai.client.token.usage", {**chat, "gen_ai.token.type": "output"})[1] == before_out + 30


def test_embedding_tokens_are_recorded_as_input_only(monkeypatch, metric_total):
    resp = SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])], usage=SimpleNamespace(prompt_tokens=7, total_tokens=7))
    monkeypatch.setattr(embed, "_get_client", lambda: _client(lambda **kw: resp))
    emb = {"gen_ai.operation.name": "embeddings", "gen_ai.request.model": settings.EMBED_MODEL}
    before = metric_total("gen_ai.client.token.usage", emb)
    assert embed.embed_batch(["x"]) == [[0.1]]
    after = metric_total("gen_ai.client.token.usage", emb)
    assert after == (before[0] + 1, before[1] + 7)
    assert metric_total("gen_ai.client.token.usage", {**emb, "gen_ai.token.type": "output"})[0] == 0
