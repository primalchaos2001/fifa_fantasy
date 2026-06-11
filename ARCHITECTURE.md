# Architecture & Code Walkthrough

A developer-level guide to how the FIFA World Cup Fantasy 2026 recommendation engine works.
For setup and CLI usage see [README.md](README.md); for the original design brief see the handover
doc. This document explains the data flow, every module, and the key algorithms.

---

## 1. The core idea

Three signals decide tournament fantasy points: **does the player actually start**, **team strength
vs. opponent**, and **set-piece/penalty involvement**. So instead of a clever model on noisy data,
this tool feeds a simple, transparent expected-points formula with *fresh, accurate* facts pulled from
free sources, weights every player by how long their team is likely to survive, and lets an integer
program pick the optimal squad. Every recommendation carries **receipts** (which headline caused it),
and nothing is ever auto-submitted.

Design rules baked into the code: **no ML**, every function dumb and debuggable, **fail loudly**
(staleness flags, never silent stale data), and a single optional LLM call used strictly as a
text→JSON parser.

---

## 2. End-to-end data flow

```
                 ┌──────────────────── INPUTS ────────────────────┐
 play.fifa.com   │ sources.load_game_data()  → GameData            │
 (public JSON) ──┤   players.json + rounds.json                    │
                 │   → Player[], Fixture[], teams, groups, stage   │
 free RSS /      │ sources.gather_news() → headlines[]             │
 Google News ────┤ extract.extract_events() → Event[]  (keyword)   │
                 │ player_state.compute_states() → PlayerState[]   │
 Elo seed ───────┤ sources.team_xg_lookup() → TeamStrength per fixture
                 └────────────────────────────────────────────────┘
                                      │
                 ┌──────────────────── COMPUTE ───────────────────┐
                 │ advance.simulate() → P(team alive at stage)     │  (Monte Carlo)
                 │ xpts.xpts_for_match() → expected pts / match     │
                 │ xpts.horizon_values() → Σ xpts × P(alive)        │  ← the optimizer's currency
                 └────────────────────────────────────────────────┘
                                      │
                 ┌──────────────────── DECIDE ────────────────────┐
                 │ optimize.pick / transfer / select_xi (PuLP ILP) │
                 └────────────────────────────────────────────────┘
                                      │
                 ┌──────────────────── EXPLAIN ───────────────────┐
                 │ report.* → receipted markdown → terminal + file │
                 │ main.py CLI: update | recommend | transfer | ...│
                 └────────────────────────────────────────────────┘
```

The boundary after INPUTS is deliberate: everything downstream consumes the frozen dataclasses in
`models.py`, so the compute/decide/explain stack is fully testable offline against fixtures.

---

## 3. Data sources & the reconciliation gotcha

The official game is a JS app backed by **public, no-auth JSON** (verified live, June 2026):

| Endpoint | What it gives |
|---|---|
| `play.fifa.com/json/fantasy/players.json` | 1485 players: `id`, name, `squadId`, `position`, `price`, `status` (playing/transferred), **`matchStatus`** (start/sub/not_in_squad), `percentSelected` (ownership), `stats.roundPoints` (actuals) |
| `play.fifa.com/json/fantasy/rounds.json` | 8 rounds with `stage` (GROUP×3, R32, R16, QF, SF, F), `startDate`/`endDate` (deadlines), and `tournaments[]` = fixtures with team ids/names, kickoff, scores |
| `play.fifa.com/json/fantasy/squads_fifa.json` | **STALE — do not use.** 32 teams, unrelated large ids, zero overlap with player `squadId`. |

**Key reconciliation:** players carry only a numeric `squadId`. The authoritative `squadId → country`
map is built from the **fixtures** (`_team_map_from_fixtures` in [sources.py](wc_fantasy/sources.py)) —
48 teams, every player `squadId` covered. **Group membership** is derived from the group-stage fixtures
by connected-components (`_derive_groups`): the four teams that all play one another form a group →
exactly 12 groups of 4. We never trust `squads_fifa.json`.

**The free lineup gift:** `matchStatus` populates ~1h before kickoff with the official confirmed XI
(`start`/`sub`/`not_in_squad`). This is the highest-trust availability signal and needs no scraping —
`xpts.official_p_start` maps it straight to a baseline `p_start`.

---

## 4. The data contract — `models.py`

Stdlib-only dataclasses every module imports (frozen early so nothing drifts):

- **`Player`** — static + official baseline: `id, name, country, squad_id, position, price, ownership,
  total_points, status, match_status, round_points`.
- **`Prior`** — hand-seeded per-90 priors: `goal_share, assist_share, penalty_taker, setpiece_taker,
  stat_baseline` (from `priors.yaml`, else price-derived).
- **`PlayerState`** — mutable, news-updated: `p_start, fit_mult, status, last_events`.
- **`Event`** — one extracted availability fact: `player, team, event_type, confidence, source_title,
  source_url, source_kind, timestamp`.
- **`Fixture`** — a match: `match_id, stage, round_id, home/away_squad_id, home, away, kickoff, status,
  home/away_score`, plus `.is_played`.
- **`TeamStrength`** — `lambda_for, lambda_against, p_win/draw/loss` per fixture.
- **`Squad`**, and **`FetchError`** (carries source + last-good timestamp so staleness propagates).

`constants.py` holds the **game rules as data** (scoring table, country-cap-by-stage, formation ranges,
`MATCHSTATUS_P_START`, horizon discount, fallback bracket) — anything uncertain is tagged `# VERIFY`.

---

## 5. Module-by-module

| Module | Responsibility |
|---|---|
| [`sources.py`](wc_fantasy/sources.py) | Fetch (official JSON + RSS/Google News + Elo), normalize to the contract, derive teams/groups, compute team strength, write snapshots, raise `FetchError`. Offline-aware (falls back to newest snapshot/fixture, flags staleness). |
| [`constants.py`](wc_fantasy/constants.py) | Scoring point table, budgets, country caps, formations, `MATCHSTATUS_P_START`, stage order, horizon discount. |
| [`scoring.py`](wc_fantasy/scoring.py) | Typed accessors over the point table + `scouting_bonus_ev` + `expected_conceded_penalty` + `verify_against_actuals`. |
| [`xpts.py`](wc_fantasy/xpts.py) | Priors (yaml or price-derived), default player states from official fields, `xpts_for_match`, and `horizon_values`. |
| [`advance.py`](wc_fantasy/advance.py) | Monte-Carlo tournament sim → `simulate()` = P(team alive at each stage), plus `advancement_table`/`render_table`. |
| [`optimize.py`](wc_fantasy/optimize.py) | PuLP ILPs: `pick` (best 15), `transfer` (with −3-hit math), `select_xi` (XI + captain + bench). |
| [`extract.py`](wc_fantasy/extract.py) | Headlines → `Event[]`: dedupe, drop >48h, keyword classify, name-match; optional gated Haiku LLM. |
| [`player_state.py`](wc_fantasy/player_state.py) | Apply events to the official baseline with trust/recency/conservatism + official override; write `events.log`. |
| [`report.py`](wc_fantasy/report.py) | Render receipted markdown (squad, XI, transfers, marginal table) → terminal + `data/reports/`. |
| [`main.py`](wc_fantasy/main.py) | CLI, the `Context` that assembles the pipeline, state persistence, automation (`schedule`). |

---

## 6. Key algorithms

### 6.1 Team strength: Elo → Poisson λ  (`sources.team_xg_lookup`)
A seeded national-team Elo table (`ELO_SEED`, free, offline; live Elo is a future swap) gives each
match an expected goal supremacy: `supremacy = (elo_home − elo_away) × 0.0035`. Split a base total of
`2.6` goals: `λ_home = 1.3 + supremacy/2`, `λ_away = 1.3 − supremacy/2` (floored at 0.2). `_poisson_1x2`
sums independent Poisson scorelines (grid to 8 goals) for win/draw/loss. `team_xg_lookup` returns a
`TeamStrength` from **each team's** perspective per unplayed fixture — exactly what xpts needs.

### 6.2 Expected points per match  (`xpts.xpts_for_match`)
The handover formula, verbatim:
```
base = appearance
     + goal_share   · team_xG · goal_pts(pos)
     + assist_share · team_xG · assist_pts
     + exp(−λ_against) · clean_sheet_pts(pos)      # clean-sheet probability
     + expected_conceded_penalty(pos, λ_against)    # GK/DEF: −1 per goal beyond the first
     + stat_baseline(pos)                            # SoT + chances + tackles prior
     + penalty_bump
xpts  = p_start · base · fit_mult
xpts += scouting_bonus_ev(ownership, xpts)           # +EV only if ownership < 5%
```
`team_xG` is `λ_for`; the clean-sheet term is `exp(−λ_against)`. Priors blend toward tournament actuals
as matchdays accrue (weight `n/(n+3)`). The **scouting bonus** (a <5%-owned player returning 4+ pts) is
modeled as a logistic-on-xpts EV term — its *presence* is what nudges the optimizer toward differentials.

### 6.3 Horizon value — the optimizer's currency  (`xpts.horizon_values`)
A player is only worth points while his team is alive. So:
```
horizon(player) = Σ remaining concrete group fixtures:  xpts(match) · 0.8^stage_offset
                + Σ future knockout stages:             P(team alive at stage) · neutral_xpts · 0.8^stage_offset
```
`P(team alive at stage)` comes from `advance.simulate()`; if the sim is unavailable it falls back to a
generic per-stage prior. This is the mechanism that **zeroes eliminated teams** and stops you loading up
on stars from teams 40% likely to be out in a week. The next round is full weight; later stages are
discounted `0.8` per stage to reflect news uncertainty.

### 6.4 Monte-Carlo advancement  (`advance.py`, Fable-authored)
~10k simulations (0.7s, stdlib only). Each sim: sample unplayed group fixtures with Poisson(λ) goals
(played results are fixed facts), rank each group by points → GD → GF → random, take **top 2 + 8 best
third-placed** → 32 qualifiers, then play single-elimination rounds deciding ties by Elo win
probability. Counts of "reached stage S" / "won final" → probabilities.
**Approximations (documented in the module):** the knockout bracket is *randomly paired* each round
because the real draw isn't published until the group stage ends (`rounds.json` has zero knockout
fixtures); when those appear, read the real bracket. FIFA tiebreakers beyond GD/GF aren't modeled. These
barely move horizon value. Pass `seed=` for reproducible sims.

### 6.5 The optimizer  (`optimize.py`)
A shared ILP (`_solve_squad`) maximizes Σ horizon value subject to: exactly 15 = 2 GK/5 DEF/5 MID/3 FWD,
budget (stage-correct 100→105 at R32), and ≤ N players per country (stage-correct).
- **`pick`** — best 15 from scratch (the "ideal team" benchmark).
- **`transfer`** — the main use. Solves the **family** k = 0,1,2,… allowed changes from your current
  squad, dedupes by transfer count, and picks the plan with the best `gross_gain − 3·max(0, k−free)`.
  This produces a transparent **marginal-value-of-each-hit** table so the −3 decision is auditable.
- **`select_xi`** — from the 15, pick 11 satisfying formation ranges (GK==1, DEF 3-5, MID 2-5, FWD 1-3),
  captain = highest next-match xpts (doubled), bench ordered by xpts with the reserve GK last.

### 6.6 News → events → state  (`extract.py` + `player_state.py`)
**Extraction (default = free keyword path):** dedupe by normalized-title hash, drop items >48h, then an
ordered keyword classifier maps text → one of `ruled_out / suspension / returned_to_training /
confirmed_start / benched / penalty_taker_change / injury_doubt`. Player names are matched against the
official list with **word boundaries** (so "Bell" ≠ "Bellingham"); a bare-surname match additionally
requires the player's **country** in the text (so "Thomas" ≠ "Thomas Tuchel"). An optional batched Haiku
call (`claude-haiku-4-5`, gated by `extract.llm_extract` + `ANTHROPIC_API_KEY`) can replace it, falling
back to keywords on any error.

**State machine:** starts from the official baseline (`official_p_start`), then applies events weighted
by **trust** (official 1.0 > lineup 0.8 > major news 0.7 > Google News 0.5 > low 0.3) × a confidence
factor. Rules encoded in `_apply_event`:
- `ruled_out`/`suspension` (high trust) → `p_start = 0`, sticky across runs until an explicit positive.
- `injury_doubt` → reduce, but the **conservatism floor** stops a single low-trust doubt sinking a
  nailed starter below 0.5.
- `confirmed_start`/`returned_to_training` → raise toward 1.
- **Official override:** an official `not_in_squad`/`transferred` forces `p_start = 0` regardless of
  positive news (news supplements, never contradicts the game downward-to-available).
Every mutation is appended to `data/events.log` (timestamp, player, old→new, headline, URL) — the
debugging lifeline. Recency decay is implicit: items age out of the 48h window and fade to baseline.

---

## 7. State & files on disk

| Path | Role | In git? |
|---|---|---|
| `data/snapshots/` | Timestamped raw pulls (offline cache) | ignored |
| `data/fixtures/` | Hand-trimmed sample data for offline tests | **committed** |
| `data/reports/<date>.md` | Receipted reports from `update` | ignored |
| `data/state.json` | Your 15-man squad + serialized player states | ignored |
| `data/events.log` | Append-only audit of every state change | ignored |
| `config.yaml` / `priors.yaml` | Configuration + player priors | committed |

---

## 8. CLI  (`main.py`)
`update` (full pipeline → dated report), `pick`, `recommend`, `transfer`, `status`, `simulate`,
`set-squad --ids ...`, `schedule`, and `--offline`. `Context` assembles everything: it loads game data,
builds priors and states (running the news pipeline only on `update`), runs the Monte Carlo, and
computes horizon + next-match xpts. `update` runs news + 10k sims; interactive commands use the fast
official baseline + 4k sims and honour any sticky news negatives from the last update.

---

## 9. Automation
`main.py schedule` prints/registers a daily Windows Task (`schtasks`) running `update`, which is
idempotent and cron-safe. Deadlines are read live from `rounds.json` so the pre-deadline window can be
tightened to hourly.

---

## 10. Tests  (`tests/`, 47 passing)
Fully offline (monkeypatched to never hit the network). `test_optimize` (constraints never violated +
the −3 hit math), `test_xpts` (Poisson favourite, clean-sheet = `exp(−λ)`, scouting bonus, monotonic in
p_start), `test_extract` (correct event types, no surname false positives), `test_player_state`
(conservatism floor, official override, sticky negatives), `test_sources` (team/group derivation,
strength symmetry, offline never fetches), `test_advance` (monotonic survival, R32 mass = 32).
Run: `py -m pytest -q`.

---

## 11. Known approximations & extension points
- **`# VERIFY` constants** — scoring table, country-cap-by-stage, formation set were taken from public
  2026 guides; spot-check against `play.fifa.com/fantasy/help`. The tool self-checks computed points vs
  official `roundPoints` once matches are played (`scoring.verify_against_actuals`).
- **Random knockout bracket** — replace with the real draw once `rounds.json` carries knockout fixtures.
- **Seeded Elo** — swap `ELO_SEED` for a live Elo/odds feed (the `sources.strength` config slot exists).
- **`priors.yaml`** — 45 penalty-takers are flagged `# VERIFY pen`; auto per-90 ingestion (FBref/
  Understat) would remove the manual seeding (handover scope-of-improvement #2).
- **Lineup scraping** (`fetch_lineups`) is best-effort and currently superseded by official `matchStatus`.
