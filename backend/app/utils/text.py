import re
from collections import Counter


_WS_RE = re.compile(r"[ \t]{2,}")
_PAGE_NUM_RE = re.compile(r"^\s*\d+\s*$")
_BULLET_ONLY_RE = re.compile(r"^[\s•●○◦▪▫◆■□]+$")


def clean_text(raw: str, page_lines: list[list[str]] | None = None) -> tuple[str, dict]:
    """Return (cleaned_text, stats). If page_lines is provided, drop repeated
    header/footer lines that appear on >50% of pages."""
    chars_before = len(raw)
    repeated = _find_repeated_headers(page_lines) if page_lines else set()

    out_lines: list[str] = []
    dropped: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            out_lines.append("")
            continue
        if _PAGE_NUM_RE.match(stripped):
            dropped.append(stripped)
            continue
        if _BULLET_ONLY_RE.match(stripped):
            dropped.append(stripped)
            continue
        if stripped in repeated:
            dropped.append(stripped)
            continue
        collapsed = _WS_RE.sub(" ", line)
        out_lines.append(collapsed)

    # Collapse runs of blank lines to a single blank line.
    cleaned: list[str] = []
    prev_blank = False
    for ln in out_lines:
        is_blank = ln.strip() == ""
        if is_blank and prev_blank:
            continue
        cleaned.append(ln)
        prev_blank = is_blank

    text = "\n".join(cleaned).strip()
    return text, {
        "chars_before": chars_before,
        "chars_after": len(text),
        "dropped_sample": dropped[:10],
    }


def _find_repeated_headers(page_lines: list[list[str]]) -> set[str]:
    if not page_lines or len(page_lines) < 2:
        return set()
    counter: Counter[str] = Counter()
    for lines in page_lines:
        seen = set()
        for ln in lines[:3] + lines[-3:]:
            s = ln.strip()
            if s and len(s) <= 120 and s not in seen:
                counter[s] += 1
                seen.add(s)
    threshold = len(page_lines) / 2
    return {s for s, c in counter.items() if c > threshold}
