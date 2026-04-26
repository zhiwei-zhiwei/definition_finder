import json
from typing import Any


def sse_event(event: str, data: Any) -> dict[str, str]:
    """Shape a dict compatible with sse_starlette's EventSourceResponse.

    Always JSON-encode the payload so leading spaces / embedded newlines
    in LLM token strings round-trip intact through the SSE framing."""
    payload = json.dumps(data, ensure_ascii=False)
    return {"event": event, "data": payload}
