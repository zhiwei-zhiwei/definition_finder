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

# Run and node outcomes: ok | paused | error | cancelled. Metric attributes stay low-cardinality
# (no query or doc ids; those are on the spans).
_SECONDS = [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300]  # prepare can take minutes
RUNS = meter.create_counter("lexis.query.runs", unit="{run}",
                            description="Query graph runs (new queries and resumes) by outcome")
RUN_DURATION = meter.create_histogram("lexis.query.duration", unit="s", description="Query graph run time",
                                      explicit_bucket_boundaries_advisory=_SECONDS)
NODE_DURATION = meter.create_histogram("lexis.node.duration", unit="s", description="Graph node attempt time",
                                       explicit_bucket_boundaries_advisory=_SECONDS)
# GenAI semantic conventions' name and buckets, so stock LLM dashboards read it.
TOKENS = meter.create_histogram("gen_ai.client.token.usage", unit="{token}", description="OpenAI tokens per call",
                                explicit_bucket_boundaries_advisory=[4 ** i for i in range(14)])
# Best child cosine over the doc, the evidence gate's signal; a bucket edge sits on its threshold.
TOP_COSINE = meter.create_histogram(
    "lexis.retrieval.top_cosine", unit="1", description="Best passage cosine similarity per retrieval",
    explicit_bucket_boundaries_advisory=sorted({round(i * 0.05, 2) for i in range(1, 21)}
                                               | {settings.EVIDENCE_MIN_COSINE}))


def record_tokens(operation: str, model: str, usage) -> None:
    """`usage` from an OpenAI response (None when the API sent none)."""
    if usage is None:
        return
    attrs = {"gen_ai.provider.name": "openai", "gen_ai.operation.name": operation, "gen_ai.request.model": model}
    TOKENS.record(usage.prompt_tokens, {**attrs, "gen_ai.token.type": "input"})
    if operation == "chat":
        TOKENS.record(usage.completion_tokens, {**attrs, "gen_ai.token.type": "output"})

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
