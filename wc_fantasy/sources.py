"""Data acquisition — the official public JSON is the backbone; news/strength supplement it.

Every fetcher: returns parsed data, writes the raw response to data/snapshots/<source>/,
retries with timeout, and raises FetchError that the report surfaces as a staleness flag.
Never silently return stale/empty data the optimizer would then trust.

The official endpoints (players.json, rounds.json) are public and need no auth. They give
players, prices, ownership, availability, fixtures, deadlines, stages, and live results.
The authoritative team mapping (squad_id -> country) is derived from the FIXTURES, because
squads_fifa.json is stale (32 teams, unrelated ids). Group membership is derived from the
group-stage fixtures via connectivity.
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from .models import Player, Fixture, TeamStrength, FetchError
from . import constants as C

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SNAP_DIR = DATA_DIR / "snapshots"
FIXTURE_DIR = DATA_DIR / "fixtures"
CONFIG_PATH = ROOT / "config.yaml"


# ---------------------------------------------------------------------------
# Config + small IO helpers
# ---------------------------------------------------------------------------

def load_config(path: Path | str | None = None) -> dict:
    p = Path(path) if path else CONFIG_PATH
    with open(p, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _write_snapshot(source: str, name: str, raw: bytes | str) -> Path:
    d = SNAP_DIR / source
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}_{_ts_slug()}.json"
    mode = "wb" if isinstance(raw, bytes) else "w"
    kwargs = {} if isinstance(raw, bytes) else {"encoding": "utf-8"}
    with open(path, mode, **kwargs) as fh:
        fh.write(raw)
    return path


def _latest_snapshot(source: str, name: str) -> Optional[Path]:
    d = SNAP_DIR / source
    if not d.exists():
        return None
    candidates = sorted(d.glob(f"{name}_*.json"))
    return candidates[-1] if candidates else None


# ---------------------------------------------------------------------------
# Low-level fetch with retries + offline fallback to the newest snapshot/fixture
# ---------------------------------------------------------------------------

def fetch_json(url: str, *, source: str, name: str, timeout: int = 30,
               retries: int = 3, offline: bool = False,
               fixture: Optional[Path] = None) -> tuple[object, Optional[str]]:
    """Fetch JSON from `url`, snapshotting the raw bytes.

    Returns (parsed, stale_since) where stale_since is None on a fresh fetch, or the
    snapshot/fixture timestamp when we fell back to cached data (so the report can flag it).
    Raises FetchError only if no fresh data AND no cached fallback exists.
    """
    if not offline:
        last_err = None
        for attempt in range(1, retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "wc-fantasy/0.1"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    raw = resp.read()
                _write_snapshot(source, name, raw)
                return json.loads(raw.decode("utf-8")), None
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                last_err = exc
                time.sleep(min(2 ** attempt, 8))
        # fetch failed -> fall through to cached fallback, flagging staleness
        fallback = _latest_snapshot(source, name) or fixture
        if fallback and fallback.exists():
            with open(fallback, encoding="utf-8") as fh:
                return json.load(fh), _fallback_ts(fallback)
        raise FetchError(source, f"fetch failed and no cache: {last_err}")

    # offline mode: prefer newest snapshot, else fixture
    fallback = _latest_snapshot(source, name) or fixture
    if fallback and fallback.exists():
        with open(fallback, encoding="utf-8") as fh:
            return json.load(fh), _fallback_ts(fallback)
    raise FetchError(source, "offline and no cached snapshot/fixture available")


def _fallback_ts(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Normalized game data container
# ---------------------------------------------------------------------------

@dataclass
class GameData:
    players: list[Player]
    fixtures: list[Fixture]
    teams: dict[int, str]                    # squad_id -> country name
    team_abbr: dict[int, str]                # squad_id -> abbreviation
    groups: dict[str, list[int]]             # group label -> [squad_id]
    rounds_meta: list[dict]                  # id, stage, start/end, status
    current_round_id: int                    # the next actionable round
    current_stage: str
    next_deadline: Optional[str]
    fetched_at: str = field(default_factory=_now_iso)
    stale: dict[str, str] = field(default_factory=dict)  # source -> stale_since ts

    # --- convenience lookups ---
    def player_by_id(self, pid: int) -> Optional[Player]:
        return next((p for p in self.players if p.id == pid), None)

    def team_of(self, squad_id: int) -> str:
        return self.teams.get(squad_id, f"team{squad_id}")

    def budget(self) -> float:
        return C.BUDGET_BY_STAGE.get(self.current_stage, 100.0)

    def country_cap(self) -> int:
        return C.COUNTRY_CAP_BY_STAGE.get(self.current_stage, 3)


# ---------------------------------------------------------------------------
# Normalization of the official payloads
# ---------------------------------------------------------------------------

def _full_name(p: dict) -> str:
    if p.get("knownName"):
        return p["knownName"]
    first, last = p.get("firstName") or "", p.get("lastName") or ""
    return (f"{first} {last}").strip() or f"player{p.get('id')}"


def _team_map_from_fixtures(rounds: list[dict]) -> tuple[dict[int, str], dict[int, str]]:
    names: dict[int, str] = {}
    abbrs: dict[int, str] = {}
    for r in rounds:
        for t in r.get("tournaments", []):
            for side in ("home", "away"):
                sid = t.get(f"{side}SquadId")
                if sid is not None:
                    names[sid] = t.get(f"{side}SquadName") or names.get(sid, f"team{sid}")
                    abbrs[sid] = t.get(f"{side}SquadAbbr") or abbrs.get(sid, "")
    return names, abbrs


def _derive_groups(fixtures: list[Fixture]) -> dict[str, list[int]]:
    """Reconstruct the 12 groups of 4 from group-stage fixtures via connectivity.

    Each group's four teams all play one another in the group stage, so the connected
    components of the "played-in-group-stage" graph are exactly the groups.
    """
    adj: dict[int, set[int]] = {}
    for f in fixtures:
        if f.stage != "GROUP":
            continue
        adj.setdefault(f.home_squad_id, set()).add(f.away_squad_id)
        adj.setdefault(f.away_squad_id, set()).add(f.home_squad_id)

    seen: set[int] = set()
    components: list[list[int]] = []
    for node in adj:
        if node in seen:
            continue
        stack, comp = [node], []
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            comp.append(n)
            stack.extend(adj[n] - seen)
        components.append(sorted(comp))

    components.sort(key=lambda c: c[0])
    labels = [chr(ord("A") + i) for i in range(len(components))]
    return {labels[i]: comp for i, comp in enumerate(components)}


def normalize_fixtures(rounds: list[dict]) -> list[Fixture]:
    fixtures: list[Fixture] = []
    for r in rounds:
        stage = r.get("stage", C.ROUND_STAGE.get(r.get("id"), "GROUP"))
        rid = r.get("id")
        for t in r.get("tournaments", []):
            fixtures.append(Fixture(
                match_id=t["id"],
                stage=stage,
                round_id=rid,
                home_squad_id=t["homeSquadId"],
                away_squad_id=t["awaySquadId"],
                home=t.get("homeSquadName", ""),
                away=t.get("awaySquadName", ""),
                kickoff=t.get("date", ""),
                status=t.get("status", "scheduled"),
                home_score=t.get("homeScore"),
                away_score=t.get("awayScore"),
            ))
    return fixtures


def normalize_players(raw: list[dict], teams: dict[int, str]) -> list[Player]:
    players: list[Player] = []
    for p in raw:
        stats = p.get("stats") or {}
        players.append(Player(
            id=p["id"],
            name=_full_name(p),
            country=teams.get(p["squadId"], f"team{p['squadId']}"),
            squad_id=p["squadId"],
            position=p["position"],
            price=float(p.get("price", 0.0)),
            ownership=float(p.get("percentSelected", 0.0)),
            total_points=int(stats.get("totalPoints", 0)),
            status=p.get("status", "playing"),
            match_status=p.get("matchStatus"),
            round_points=tuple(stats.get("roundPoints", []) or ()),
        ))
    return players


def _pick_current_round(rounds_meta: list[dict]) -> tuple[int, str, Optional[str]]:
    """The next actionable round = first whose start (deadline) is in the future; else last."""
    now = datetime.now(timezone.utc)
    upcoming = []
    for r in rounds_meta:
        try:
            start = datetime.fromisoformat(r["startDate"])
        except (KeyError, ValueError):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        upcoming.append((start, r))
    upcoming.sort(key=lambda x: x[0])
    for start, r in upcoming:
        if start > now:
            return r["id"], r.get("stage", "GROUP"), r["startDate"]
    # all started -> use the last round
    last = upcoming[-1][1] if upcoming else {"id": 1, "stage": "GROUP", "startDate": None}
    return last["id"], last.get("stage", "GROUP"), last.get("startDate")


def load_game_data(config: Optional[dict] = None, *, offline: bool = False) -> GameData:
    """Fetch + normalize the official data into a single GameData object."""
    cfg = config or load_config()
    off = cfg.get("official", {})
    timeout = off.get("timeout_sec", 30)
    retries = off.get("retries", 3)
    stale: dict[str, str] = {}

    players_raw, s1 = fetch_json(
        off["players_url"], source="fantasy", name="players", timeout=timeout,
        retries=retries, offline=offline, fixture=FIXTURE_DIR / "sample_players.json")
    rounds_raw, s2 = fetch_json(
        off["rounds_url"], source="fantasy", name="rounds", timeout=timeout,
        retries=retries, offline=offline, fixture=FIXTURE_DIR / "sample_rounds.json")
    if s1:
        stale["players"] = s1
    if s2:
        stale["rounds"] = s2

    teams, abbrs = _team_map_from_fixtures(rounds_raw)
    fixtures = normalize_fixtures(rounds_raw)
    players = normalize_players(players_raw, teams)
    groups = _derive_groups(fixtures)
    rounds_meta = [{k: v for k, v in r.items() if k != "tournaments"} for r in rounds_raw]
    cur_id, cur_stage, deadline = _pick_current_round(rounds_meta)

    return GameData(
        players=players, fixtures=fixtures, teams=teams, team_abbr=abbrs,
        groups=groups, rounds_meta=rounds_meta, current_round_id=cur_id,
        current_stage=cur_stage, next_deadline=deadline, stale=stale,
    )


# ---------------------------------------------------------------------------
# Team strength -> Poisson goal expectations.
#
# Default (free, offline) path uses a seeded national-team Elo table. Phase 3 adds a
# live Elo/odds fetch that overrides this. Approximate seed values (mid-2026 ballpark);
# missing teams default to BASELINE_ELO.  # VERIFY against eloratings.net
# ---------------------------------------------------------------------------

BASELINE_ELO = 1700.0
BASE_TOTAL_GOALS = 2.6           # average goals per match (both teams)
ELO_TO_SUPREMACY = 0.0035        # goal supremacy per Elo point of difference
MIN_LAMBDA = 0.2

ELO_SEED: dict[str, float] = {
    "Argentina": 2130, "France": 2080, "Spain": 2070, "England": 2025, "Brazil": 2030,
    "Portugal": 2000, "Netherlands": 1985, "Belgium": 1940, "Germany": 1960, "Italy": 1940,
    "Croatia": 1900, "Uruguay": 1900, "Colombia": 1880, "Morocco": 1870, "Switzerland": 1850,
    "USA": 1800, "Mexico": 1800, "Senegal": 1820, "Japan": 1810, "Korea Republic": 1760,
    "Denmark": 1860, "Austria": 1820, "Ecuador": 1800, "Nigeria": 1800, "Serbia": 1810,
    "Australia": 1740, "Canada": 1760, "Poland": 1790, "Egypt": 1780, "Algeria": 1790,
    "Norway": 1880, "Sweden": 1800, "Ukraine": 1810, "Turkey": 1820, "Czechia": 1790,
    "Wales": 1780, "Ghana": 1740, "Cameroon": 1730, "Tunisia": 1720, "Iran": 1760,
    "Saudi Arabia": 1660, "Qatar": 1640, "South Africa": 1700, "Paraguay": 1740,
    "Cote d'Ivoire": 1750, "Cabo Verde": 1640, "Congo DR": 1700, "Bosnia and Herzegovina": 1760,
}


def _elo(team: str, table: dict[str, float]) -> float:
    return table.get(team, BASELINE_ELO)


def team_strengths(gd: GameData, *, elo_table: Optional[dict[str, float]] = None,
                   home_adv_goals: float = 0.0) -> dict[int, TeamStrength]:
    """One TeamStrength per *unplayed* fixture, keyed by match_id, for the team in focus.

    We return strength from each team's perspective for its next fixtures via a dict
    keyed by (match_id) giving the home side; callers needing a specific team use
    strength_for_team(). For simplicity xpts uses team_xg_lookup() below.
    """
    table = elo_table or ELO_SEED
    out: dict[int, TeamStrength] = {}
    for f in gd.fixtures:
        if f.is_played:
            continue
        diff = _elo(f.home, table) - _elo(f.away, table) + (home_adv_goals / ELO_TO_SUPREMACY)
        supremacy = diff * ELO_TO_SUPREMACY
        lam_home = max(MIN_LAMBDA, BASE_TOTAL_GOALS / 2 + supremacy / 2)
        lam_away = max(MIN_LAMBDA, BASE_TOTAL_GOALS / 2 - supremacy / 2)
        pw, pd, pl = _poisson_1x2(lam_home, lam_away)
        out[f.match_id] = TeamStrength(
            match_id=f.match_id, team=f.home, opponent=f.away,
            lambda_for=lam_home, lambda_against=lam_away,
            p_win=pw, p_draw=pd, p_loss=pl,
        )
    return out


def team_xg_lookup(gd: GameData, *, elo_table: Optional[dict[str, float]] = None,
                   home_adv_goals: float = 0.0) -> dict[tuple[int, int], TeamStrength]:
    """Map (squad_id, match_id) -> TeamStrength from that team's perspective.

    This is what xpts.py consumes: for each player's team and each of its fixtures,
    look up lambda_for (team xG) and lambda_against (-> clean-sheet prob).
    """
    table = elo_table or ELO_SEED
    out: dict[tuple[int, int], TeamStrength] = {}
    for f in gd.fixtures:
        if f.is_played:
            continue
        diff = _elo(f.home, table) - _elo(f.away, table) + (home_adv_goals / ELO_TO_SUPREMACY)
        supremacy = diff * ELO_TO_SUPREMACY
        lam_home = max(MIN_LAMBDA, BASE_TOTAL_GOALS / 2 + supremacy / 2)
        lam_away = max(MIN_LAMBDA, BASE_TOTAL_GOALS / 2 - supremacy / 2)
        pw, pd, pl = _poisson_1x2(lam_home, lam_away)
        out[(f.home_squad_id, f.match_id)] = TeamStrength(
            f.match_id, f.home, f.away, lam_home, lam_away, pw, pd, pl)
        out[(f.away_squad_id, f.match_id)] = TeamStrength(
            f.match_id, f.away, f.home, lam_away, lam_home, pl, pd, pw)
    return out


def _poisson_1x2(lam_home: float, lam_away: float, max_goals: int = 8) -> tuple[float, float, float]:
    """Win/draw/loss probabilities from independent Poisson scorelines."""
    import math
    def pmf(k, lam):
        return math.exp(-lam) * lam ** k / math.factorial(k)
    pw = pd = pl = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = pmf(h, lam_home) * pmf(a, lam_away)
            if h > a:
                pw += p
            elif h == a:
                pd += p
            else:
                pl += p
    return pw, pd, pl
