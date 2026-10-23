"""OpenTelemetry wiring: every API request gets a server span and http.server metrics."""


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
