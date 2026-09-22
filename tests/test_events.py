import inspect
import json

import pytest

from rift24 import events as E
from rift24.data import load_allowlist

INS, _ = load_allowlist()


def m(h, **kw):
    return E.map_event(h, INS, **kw)


def test_company_headline_maps_to_its_rtoken():
    r = m("NVIDIA announces stronger-than-expected data-center demand")
    assert r.tickers == ("rNVDA",) and r.method == "rule" and r.relevance["rNVDA"] == "direct"
    assert "nvidia" in r.matched_terms["rNVDA"]


def test_unmapped_headline_produces_no_stance_not_a_guess():
    r = m("Regional bakery chain wins national award for sourdough")
    assert r.tickers == () and r.method == "none" and "NO ALLOW-LISTED TICKER" in r.note


def test_macro_headline_maps_to_index_rtokens_only():
    r = m("Fed signals a rate cut as Treasury yields slide")
    assert set(r.tickers) == {"rSPY", "rQQQ"} and all(v == "macro" for v in r.relevance.values())


def test_direct_and_macro_can_combine_and_direct_wins_the_label():
    r = m("AMD jumps as oil prices fall and Treasury yields dip")
    assert r.relevance["rAMD"] == "direct" and r.relevance["rSPY"] == "macro"
    assert r.tickers[0] == "rAMD"


def test_word_boundaries_avoid_substring_false_positives():
    assert m("The metaverse hype cycle continues").tickers == ()  # 'meta' inside 'metaverse'
    assert m("Snapdragon beats amdocs forecast").tickers == ()  # 'amd' inside 'amdocs'
    assert m("Shares of (NASDAQ:AMD) rose").tickers == ("rAMD",)  # bare 'nasdaq' must not drag in rQQQ


def test_only_allowlisted_tickers_can_ever_be_returned():
    allowed = {i.rtoken for i in INS}
    for h in ["Tesla robotaxi expands", "Apple iPhone demand", "Microsoft Azure outage", "Alphabet Gemini launch",
              "Advanced Micro Devices EPYC win", "Amazon AWS deal", "Instagram outage at Meta", "Palantir surges"]:
        assert set(m(h).tickers) <= allowed


def test_llm_path_is_off_by_default(monkeypatch):
    monkeypatch.delenv("RIFT24_LLM", raising=False)
    called = []
    r = E.map_event("Tesla robotaxi expands", INS)
    assert r.method == "rule" and not called


def test_llm_output_is_validated_and_used_when_clean():
    def fake(_prompt):
        return json.dumps({"ticker": "rNVDA", "explanation": "The event concerns demand for NVIDIA's data-center business."})

    r = m("Chipmaker reports strong demand", client=fake)
    assert r.method == "llm" and r.tickers == ("rNVDA",) and "data-center" in r.explanation


@pytest.mark.parametrize("bad", [
    "Buy now before the open, this will surge.",
    "Price target of $900 seems reasonable.",
    "Allocate 15% of the book here.",
    "There is a 70% chance this rises.",
    "Sell immediately.",
    "x" * 500,
])
def test_llm_explanations_that_read_like_trading_advice_are_rejected(bad):
    r = m("Tesla robotaxi expands", client=lambda _p: json.dumps({"ticker": "rTSLA", "explanation": bad}))
    assert r.method == "rule" and "rejected" in r.note  # falls back to the deterministic mapper


def test_llm_cannot_introduce_a_ticker_outside_the_allowlist():
    r = m("Tesla robotaxi expands", client=lambda _p: json.dumps({"ticker": "rPLTR", "explanation": "Relevant to Palantir."}))
    assert r.method == "rule" and "rPLTR" not in r.tickers


def test_llm_failures_degrade_to_rules_instead_of_raising():
    def boom(_p):
        raise TimeoutError("no network")

    r = m("Tesla robotaxi expands", client=boom)
    assert r.method == "rule" and r.tickers == ("rTSLA",) and "unavailable" in r.note


def test_quant_engine_never_imports_the_llm_module():
    import rift24.backtest as B
    import rift24.metrics as M
    import rift24.residual as R
    for mod in (R, B, M):
        src = inspect.getsource(mod)
        assert "from .events" not in src and "import events" not in src and "rift24.events" not in src, mod.__name__


def test_event_fixtures_are_labelled_and_sourced_ones_have_urls():
    evs = E.load_events(INS)
    assert evs and all(e.provenance in ("SOURCED", "ILLUSTRATIVE") for e in evs)
    for e in evs:
        if e.provenance == "SOURCED":
            assert e.url and e.url.startswith("https://")
        else:
            assert "ILLUSTRATIVE" in e.source
    by = {e.id: e for e in evs}
    assert "rAMD" in by["src-amd-0921"].mapping.tickers and "rMETA" in by["src-meta-0921"].mapping.tickers
    assert by["ill-none"].mapping.tickers == ()
