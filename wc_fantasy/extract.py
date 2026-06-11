"""News headlines -> structured availability events.

DEFAULT path is the zero-cost keyword classifier (no API, no key). The optional batched
Anthropic call is gated behind config.extract.llm_extract + ANTHROPIC_API_KEY and always
falls back to keywords on any error — so the tool runs fully free out of the box.

Pipeline: dedupe (normalized-title hash) -> drop >48h -> classify -> fuzzy-match player
names against the official list (unmatched dropped). Each kept item yields one Event.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from typing import Optional

from .models import Event, Player

# Ordered (event_type, confidence, regex-keywords). First match wins, so strong/explicit
# signals (suspension, ruled_out, returned, confirmed) are checked before weak ones (doubt).
KEYWORD_RULES: list[tuple[str, str, list[str]]] = [
    ("suspension", "high", [r"\bsuspend", r"\bban(?:ned)?\b", r"red card", r"yellow (?:card )?accumulation"]),
    ("ruled_out", "high", [r"ruled? out", r"out of the (?:world cup|tournament)", r"\bwill miss\b",
                            r"\bsidelined\b", r"misses the", r"withdrawn from"]),
    ("returned_to_training", "high", [r"returns? to (?:full )?training", r"back in training",
                                      r"passed (?:a )?(?:late )?fitness test", r"passed fit", r"recovered"]),
    # benched is checked BEFORE confirmed_start: an explicit bench word ("benched",
    # "on the bench") is a strong negative that must win over a stray "XI" in the title.
    ("benched", "medium", [r"\bbenched\b", r"on the bench", r"\brested\b", r"among the substitutes",
                           r"named as a sub", r"starts on the bench", r"rotat"]),
    ("confirmed_start", "high", [r"\bstarts?\b", r"named in the (?:starting )?(?:xi|eleven|line)",
                                 r"in the starting", r"named to start", r"will start", r"confirmed.*start"]),
    ("penalty_taker_change", "medium", [r"penalt(?:y|ies)", r"spot[- ]kick", r"takes? over (?:the )?penalt"]),
    ("injury_doubt", "medium", [r"\bdoubt", r"fitness test", r"\blimp", r"\bknock\b", r"injury scare",
                                r"being assessed", r"race against time", r"illness"]),
]


def _norm(text: str) -> str:
    """Lowercase + strip accents so 'Mbappé' matches 'Mbappe' and titles dedupe cleanly."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_only.lower()


def _title_hash(item: dict) -> str:
    norm = re.sub(r"[^a-z0-9]+", "", _norm(item.get("title", "")))
    return hashlib.sha1(norm.encode()).hexdigest()


def dedupe(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for it in items:
        h = _title_hash(it)
        if h not in seen:
            seen.add(h)
            out.append(it)
    return out


def drop_old(items: list[dict], hours: int = 48) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    kept = []
    for it in items:
        ts = it.get("published")
        if not ts:
            kept.append(it)
            continue
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            kept.append(it)
            continue
        if dt >= cutoff:
            kept.append(it)
    return kept


# --- player-name matching against the official list -------------------------

def _name_index(players: list[Player]) -> list[tuple[re.Pattern, Player, bool]]:
    """Precompile word-boundary patterns. Each entry: (pattern, player, is_surname_only).

    Word boundaries stop 'Bell' matching inside 'Bellingham'. Surname-only entries are
    additionally guarded at match time (the player's country must also appear) so a bare
    'Thomas' doesn't match the manager 'Thomas Tuchel'. Compiled once per run.
    """
    last_counts: dict[str, int] = {}
    for p in players:
        parts = p.name.split()
        if parts:
            last_counts.setdefault(_norm(parts[-1]), 0)
            last_counts[_norm(parts[-1])] += 1

    seen_keys: set[str] = set()
    index: list[tuple[re.Pattern, Player, bool]] = []
    for p in players:
        full = _norm(p.name)
        if full and full not in seen_keys:
            seen_keys.add(full)
            index.append((re.compile(rf"\b{re.escape(full)}\b"), p, False))
        parts = p.name.split()
        if parts:
            last = _norm(parts[-1])
            if last_counts.get(last) == 1 and len(last) >= 5 and last not in seen_keys:
                seen_keys.add(last)
                index.append((re.compile(rf"\b{re.escape(last)}\b"), p, True))
    return index


def _match_players(text: str, index: list[tuple[re.Pattern, Player, bool]]) -> list[Player]:
    norm = _norm(text)
    found: dict[int, Player] = {}
    for pattern, player, surname_only in index:
        if not pattern.search(norm):
            continue
        # a bare-surname hit must also mention the player's country (precision guard)
        if surname_only and _norm(player.country) not in norm:
            continue
        found[player.id] = player
    return list(found.values())


def _classify(text: str, source_kind: str) -> tuple[str, str]:
    norm = _norm(text)
    for event_type, confidence, patterns in KEYWORD_RULES:
        if any(re.search(p, norm) for p in patterns):
            # low-trust sources cap confidence
            if source_kind in ("google_news", "low") and confidence == "high":
                confidence = "medium"
            return event_type, confidence
    return "none", "low"


def keyword_extract(items: list[dict], players: list[Player]) -> list[Event]:
    index = _name_index(players)
    events: list[Event] = []
    for it in items:
        text = f"{it.get('title', '')} {it.get('summary', '')}"
        matched = _match_players(text, index)
        if not matched:
            continue
        event_type, confidence = _classify(text, it.get("source_kind", "google_news"))
        if event_type == "none":
            continue
        for player in matched:
            events.append(Event(
                player=player.name, team=player.country, event_type=event_type,
                confidence=confidence, source_title=it.get("title", ""),
                source_url=it.get("url", ""), source_kind=it.get("source_kind", "google_news"),
                timestamp=it.get("published", datetime.now(timezone.utc).isoformat()),
            ))
    return events


# --- optional LLM extraction (opt-in, zero-cost default is keyword) ----------

_LLM_SYSTEM = (
    "From these football news headlines/snippets, extract availability events as a JSON array. "
    "Each item: {\"player\": str, \"team\": str, \"event_type\": one of "
    "[\"injury_doubt\",\"ruled_out\",\"returned_to_training\",\"suspension\",\"confirmed_start\","
    "\"benched\",\"penalty_taker_change\",\"none\"], \"confidence\": \"high\"|\"medium\"|\"low\", "
    "\"source_title\": str, \"source_url\": str}. Only concrete reported facts about player "
    "availability/role. Speculation, opinion, transfer rumours -> confidence \"low\" or omit. "
    "Output ONLY the JSON array, no prose, no code fences."
)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return text.strip()


def llm_extract(items: list[dict], players: list[Player], model: str,
                max_items: int = 120) -> Optional[list[Event]]:
    """One batched Haiku-class call. Returns None on any failure so callers fall back."""
    try:
        import anthropic  # optional dependency
    except ImportError:
        return None
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    payload = [{"title": it.get("title", ""), "summary": it.get("summary", ""),
                "url": it.get("url", "")} for it in items[:max_items]]
    try:
        client = anthropic.Anthropic()
        resp = client.messages.create(
            model=model, max_tokens=4096, system=_LLM_SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        raw = json.loads(_strip_fences(text))
    except Exception:
        return None

    index = _name_index(players)
    events: list[Event] = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict) or e.get("event_type") in (None, "none"):
            continue
        name = e.get("player", "")
        matched = _match_players(name, index)
        player = matched[0] if matched else None
        events.append(Event(
            player=player.name if player else name,
            team=player.country if player else e.get("team", ""),
            event_type=e.get("event_type", "none"),
            confidence=e.get("confidence", "low"),
            source_title=e.get("source_title", ""), source_url=e.get("source_url", ""),
            source_kind="llm", timestamp=datetime.now(timezone.utc).isoformat(),
        ))
    return events


def extract_events(items: list[dict], players: list[Player],
                   config: Optional[dict] = None) -> list[Event]:
    """Main entry: dedupe -> drop old -> classify. Keyword by default; LLM only if opted in."""
    cfg = (config or {}).get("extract", {})
    pool = drop_old(dedupe(items), hours=(config or {}).get("news", {}).get("drop_older_than_hours", 48))
    if cfg.get("llm_extract"):
        events = llm_extract(pool, players, cfg.get("model", "claude-haiku-4-5"),
                             cfg.get("max_headlines_per_call", 120))
        if events is not None:
            return events
    return keyword_extract(pool, players)
