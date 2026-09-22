"""Event -> allow-listed ticker mapping and a short explanation. This is the ONLY place an LLM may be used.

The rule-based mapper is the default and needs nothing. The LLM path is behind RIFT24_LLM=1 and is confined:

  * it may only (1) name an allow-listed ticker for a headline and (2) write a short explanation paragraph;
  * its output is validated - unknown tickers are rejected, and any text that reads like a trading instruction
    (buy/sell now, price targets, sizing, probabilities) is rejected and replaced by the rule-based result;
  * nothing it returns is ever passed to residual.decide(): the quantitative engine does not import this module.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .data import DATA_DIR, Instrument

# Macro vocabulary maps to the two index rTokens only. Kept here (not in the allow-list) so it is explicit and testable.
MACRO_TERMS = (
    "fed", "fomc", "federal reserve", "powell", "rate cut", "rate hike", "interest rate", "tariff", "tariffs",
    "treasury yield", "treasury yields", "yields", "oil prices", "crude", "inflation", "cpi", "jobs report",
    "payrolls", "geopolitical", "ceasefire", "sanctions", "futures", "wall street", "us-china", "u.s.-china",
    "strait of hormuz",
)
MACRO_TARGETS = ("rSPY", "rQQQ")


@dataclass(frozen=True)
class Mapping:
    headline: str
    tickers: tuple[str, ...]  # rToken names, e.g. 'rAMD'
    relevance: dict[str, str]  # rToken -> 'direct' | 'macro'
    matched_terms: dict[str, tuple[str, ...]]
    method: str  # 'rule' | 'llm' | 'none'
    explanation: str
    note: str = ""


def _has(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text) is not None


def map_event_rules(headline: str, instruments: list[Instrument]) -> Mapping:
    text = headline.lower()
    direct: dict[str, list[str]] = {}
    for ins in instruments:
        hits = [a for a in ins.aliases if _has(text, a)]
        if _has(text, ins.underlying.lower()) and ins.underlying.lower() not in hits:
            hits.append(ins.underlying.lower())
        if hits:
            direct[ins.rtoken] = hits
    macro_hits = [t for t in MACRO_TERMS if _has(text, t)]
    tickers, relevance, matched = [], {}, {}
    for k, v in direct.items():
        tickers.append(k)
        relevance[k] = "direct"
        matched[k] = tuple(v)
    if macro_hits:
        valid = {i.rtoken for i in instruments}
        for k in MACRO_TARGETS:
            if k in valid and k not in relevance:
                tickers.append(k)
                relevance[k] = "macro"
                matched[k] = tuple(macro_hits)
    if not tickers:
        return Mapping(headline, (), {}, {}, "none", "", "NO ALLOW-LISTED TICKER - no stance can be produced from this headline.")
    expl = _rule_explanation(headline, tickers, relevance, matched)
    return Mapping(headline, tuple(tickers), relevance, matched, "rule", expl)


def _rule_explanation(headline: str, tickers, relevance, matched) -> str:
    parts = []
    for t in tickers:
        terms = ", ".join(matched[t][:3])
        if relevance[t] == "direct":
            parts.append(f"{t}: the headline names it directly ({terms}).")
        else:
            parts.append(f"{t}: index-level exposure to the macro theme ({terms}).")
    return " ".join(parts) + " Relevance is a text match only; the size of any residual comes from Rift24's historical-reference gates, not from the headline."


# ------------------------------------------------------------------------------- optional LLM (feature-flagged)

_BANNED = re.compile(
    r"\b(buy|sell|short|long)\s+(now|immediately|today)\b|price\s+target|target\s+price|\bstop[- ]?loss\b|position\s+siz|"
    r"\ballocate\b|\bprobabilit|\bguarantee|\bwill\s+(rise|fall|surge|crash)\b|\b\d{1,3}\s?%\s+(upside|downside|chance)|\bbuy\b|\bsell\b",
    re.I,
)
MAX_EXPLANATION_CHARS = 420


def guard_explanation(text: str) -> str | None:
    """Return the text if it is an explanation, None if it reads like a trading instruction or is too long."""
    t = (text or "").strip()
    if not t or len(t) > MAX_EXPLANATION_CHARS or _BANNED.search(t):
        return None
    return t


def llm_enabled() -> bool:
    return os.environ.get("RIFT24_LLM", "") == "1"


def _default_client(prompt: str) -> str:  # pragma: no cover - needs network + key
    """OpenAI-compatible chat endpoint. Defaults target Alibaba Qwen (DashScope international)."""
    base = os.environ.get("RIFT24_LLM_BASE_URL", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1")
    model = os.environ.get("RIFT24_LLM_MODEL", "qwen-plus")
    key = os.environ["RIFT24_LLM_API_KEY"]
    body = json.dumps({"model": model, "temperature": 0, "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(f"{base}/chat/completions", body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def map_event(headline: str, instruments: list[Instrument], *, client=None) -> Mapping:
    """Rules first. With RIFT24_LLM=1 (or an injected client) the LLM may refine the ticker + write the explanation."""
    base = map_event_rules(headline, instruments)
    if client is None and not llm_enabled():
        return base
    allowed = {i.rtoken for i in instruments}
    prompt = (
        "You map a news headline to ONE ticker from this allow-list and explain the relevance in at most two sentences.\n"
        f"Allow-list: {sorted(allowed)}\n"
        'Reply with JSON only: {"ticker": "<allow-list item or null>", "explanation": "<why the headline is relevant>"}.\n'
        "Do not give trading advice, price targets, sizes or probabilities.\n"
        f"Headline: {headline}"
    )
    try:
        raw = (client or _default_client)(prompt)
        obj = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        tk, ex = obj.get("ticker"), guard_explanation(obj.get("explanation", ""))
        if tk in allowed and ex:
            rel = base.relevance.get(tk, "direct")
            return Mapping(headline, (tk,), {tk: rel}, {tk: base.matched_terms.get(tk, ())}, "llm", ex,
                           "LLM output validated: ticker on allow-list, explanation contains no trading instruction")
        return _with_note(base, "LLM output rejected by the guard; rule-based result kept")
    except Exception as e:  # noqa: BLE001 - never let the LLM path break the product
        return _with_note(base, f"LLM unavailable ({type(e).__name__}); rule-based result kept")


def _with_note(m: Mapping, note: str) -> Mapping:
    return Mapping(m.headline, m.tickers, m.relevance, m.matched_terms, m.method, m.explanation, note)


# ------------------------------------------------------------------------------- event board

@dataclass
class Event:
    id: str
    headline: str
    source: str
    url: str | None
    provenance: str  # SOURCED | ILLUSTRATIVE
    summary: str
    published_note: str
    mapping: Mapping | None = field(default=None)


def load_events(instruments: list[Instrument], path: Path | None = None, *, client=None) -> list[Event]:
    raw = json.loads((path or DATA_DIR / "fixtures" / "events.json").read_text(encoding="utf-8"))
    out = []
    for e in raw["events"]:
        ev = Event(e["id"], e["headline"], e["source"], e.get("url"), e["provenance"], e.get("summary", ""), e.get("published_note", ""))
        ev.mapping = map_event(ev.headline + " " + ev.summary, instruments, client=client)
        out.append(ev)
    return out
