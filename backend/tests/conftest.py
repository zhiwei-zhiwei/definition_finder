"""Test harness — binds the app to a throwaway SQLite DB and the console email
backend BEFORE any `app.*` import, so settings/engine pick up the test config.
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="lexisai-test-")
os.environ["SQLITE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["CHECKPOINT_DB"] = f"{_TMP}/checkpoints.db"
os.environ["EMAIL_BACKEND"] = "console"
os.environ["JWT_SECRET"] = "test-secret-test-secret-test-secret-0123"
os.environ["OPENAI_API_KEY"] = "test-key-not-used"
os.environ["FRONTEND_URL"] = "http://localhost:3000"
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = ""  # never export, whatever backend/.env says

import re  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from opentelemetry import metrics, trace  # noqa: E402
from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import SimpleSpanProcessor  # noqa: E402
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402

# Global providers can be set once per process, so the in-memory ones go in before the app
# is imported and every test reads from them.
_SPANS = InMemorySpanExporter()
_tp = TracerProvider()
_tp.add_span_processor(SimpleSpanProcessor(_SPANS))
trace.set_tracer_provider(_tp)
_METRICS = InMemoryMetricReader()
metrics.set_meter_provider(MeterProvider(metric_readers=[_METRICS]))

from app.main import app  # noqa: E402
from app.services import email as email_svc  # noqa: E402


@pytest.fixture(scope="session")
def client():
    # Context-manager form fires startup events (init_db -> tables + migration).
    with TestClient(app) as c:
        yield c


@pytest.fixture
def spans():
    """The spans finished during this test."""
    _SPANS.clear()
    return _SPANS


@pytest.fixture
def otel_metrics():
    """Reads every metric point recorded so far (cumulative: compare before and after)."""
    def read() -> dict:
        data = _METRICS.get_metrics_data()
        return {m.name: m for rm in data.resource_metrics for sm in rm.scope_metrics
                for m in sm.metrics} if data else {}
    return read


@pytest.fixture
def metric_total(otel_metrics):
    """metric_total(name, attrs) -> (count, sum) over the points whose attributes include
    `attrs`: a counter's count is its value, a histogram's the number of recordings."""
    def total(name: str, attrs: dict | None = None) -> tuple[float, float]:
        m = otel_metrics().get(name)
        points = [p for p in (m.data.data_points if m else [])
                  if (attrs or {}).items() <= dict(p.attributes).items()]
        return (sum(getattr(p, "count", getattr(p, "value", 0)) for p in points),
                sum(getattr(p, "sum", getattr(p, "value", 0)) for p in points))
    return total


def _extract_token() -> str:
    """Pull the one-time token out of the most recently 'sent' email."""
    sent = email_svc.last_sent()
    assert sent is not None, "no email was captured"
    m = re.search(r"[?&]token=([A-Za-z0-9_\-]+)", sent["text"])
    assert m, f"no link token found in email: {sent}"
    return m.group(1)


@pytest.fixture
def link_token():
    return _extract_token


@pytest.fixture
def make_user(client):
    """Sign up + verify a user; returns (email, jwt) for an active account."""

    def _make(email: str, username: str, password: str = "Sup3r!secret"):
        r = client.post(
            "/auth/signup",
            json={"email": email, "username": username, "password": password},
        )
        assert r.status_code == 201, r.text
        r2 = client.post("/auth/verify", json={"token": _extract_token()})
        assert r2.status_code == 200, r2.text
        return email, r2.json()["token"]

    return _make


@pytest.fixture
def bearer():
    return lambda jwt: {"Authorization": f"Bearer {jwt}"}
