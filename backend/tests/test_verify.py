"""verify.check flags what the answer states that its excerpts don't, and spots refusals."""
from app.services.query_graph import NO_CONTEXT
from app.services.verify import check

LEASE = [{"text": "a Market Rent of $3880.00, plus a utility fee of $190.00, for a total monthly rent of $4070.00.",
          "page_start": 7, "page_end": None}]  # page_end None: single-page parent
TENK = [{"text": "Net revenue for 2022 was $23.6 billion.", "page_start": 9, "page_end": 10}]


def test_grounded_answer_passes():
    # "$4,070.00" vs the source's "$4070.00": same number, different formatting
    assert check("The total monthly rent is $4,070.00 (page 7).", LEASE)["status"] == "ok"
    assert check("AMD does not expect to pay dividends (page 9).", TENK)["status"] == "ok"
    html = [{"text": "A refundable pet deposit of $300.00 is due.", "page_start": None, "page_end": None}]
    assert check("The lease is month to month.", html)["status"] == "ok"  # non-PDF: no pages to check
    # Non-PDF answers cite excerpts by number; that 1 is not a stated figure
    assert check("The pet deposit is $300.00 (Excerpt 1; Excerpts 1, 2, and 3).", html)["status"] == "ok"


def test_fake_page_and_unsourced_number_warn():
    r = check("Net revenue was $25 billion (page 90).", TENK)
    assert r["status"] == "warning" and r["needs_review"]
    assert [i["kind"] for i in r["issues"]] == ["fake_page", "unsourced_number"]
    assert "90" in r["issues"][0]["detail"] and "25 billion" in r["issues"][1]["detail"]


def test_refusals_are_insufficient_evidence():
    refusal = "The provided excerpts from AMD's 2022 filings do not specify revenue from Apple. Revenue was $23.6 billion."
    for answer in (refusal, NO_CONTEXT):
        assert check(answer, TENK) == {"status": "insufficient_evidence", "issues": [], "needs_review": True}
    # "does not" about the company, not the excerpts, is an answer
    assert check("The document states that AMD does not expect to pay dividends (page 9).", TENK)["status"] == "ok"
