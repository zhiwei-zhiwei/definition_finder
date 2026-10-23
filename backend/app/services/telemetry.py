"""OpenTelemetry setup. Code records spans and metrics through `tracer` and `meter`, which are
the API's proxies: with no endpoint configured they stay no-ops, and tests can install their
own in-memory providers. setup() is what makes them export, over OTLP/HTTP."""
import os

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import settings

# The HTTP instrumentation still defaults to the old semantic conventions (http.server.duration
# in ms, http.status_code); "http" switches it to the stable ones (http.server.request.duration
# in seconds, http.response.status_code), which current dashboards and docs use.
os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "http")

tracer = trace.get_tracer("lexisai")
meter = metrics.get_meter("lexisai")

_providers: list = []


def setup() -> None:
    base = settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip("/")
    if not base:
        return
    resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
    reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{base}/v1/metrics"),
                                           export_interval_millis=10_000)
    mp = MeterProvider(resource=resource, metric_readers=[reader])
    trace.set_tracer_provider(tp)
    metrics.set_meter_provider(mp)
    _providers[:] = [tp, mp]


def shutdown() -> None:
    """Flush what is still buffered (spans are batched, metrics go every 10 s)."""
    for p in _providers:
        p.shutdown()
