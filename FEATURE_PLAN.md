# Plan: Formations · Playing-XI · Per-Player Transfers (engine + web GUI)

> Implementation plan (to be built with Sonnet). Kept as an independent cross-check against
> Google Antigravity's analysis of the same three gaps. See [ARCHITECTURE.md](ARCHITECTURE.md)
> for how the codebase fits together.

## Context

The Python engine already does formations (`optimize.select_xi` enforces the 7 legal
formations via binary vars) and global transfers (`optimize.transfer`, the −3-hit ILP). But
three user-facing capabilities are **missing or fake**, mostly in the **static web GUI**
(`docs/`), which reads `data.json` and runs entirely client-side (no backend):

1. **Formations** — the GUI shows a formation *label* only; no picker, and `app.js
   generateLineup()` fills positions greedily and can emit an **illegal** formation (e.g.
   `5-2-3`, not one of the 7). The engine can't force a specific formation either.
2. **Playing XI** — the pitch (`#tab-pitch`) is static: no bench↔XI swap, no captain change,
   no manual edit.
3. **Per-player transfers** — the Transfer Planner (`#tab-transfers`) is **mock**
   (`renderTransferTab` uses hardcoded swaps + a hardcoded marginal table). There's no
   "best replacement for *this* player" and Player-DB rows aren't clickable. The engine has
   no per-player swap function.

**Goal (confirmed scope):** add the missing logic to **both** the Python engine (force-
formation, per-player swaps in **both directions**, CLI, tests) **and** the web GUI (formation
picker, interactive editable XI, real per-player transfer panels).

**Hard constraints to respect:**
- The GUI is a **static site**: all interactivity is client-side JS in `docs/app.js` over
  `data.json`. `report.save_web_json` writes it (local→`data.local.json`, CI→`data.json`; GUI
  fetches local first). Per-player swaps depend on the user's *client-built* squad, so they
  **must** be computed in JS — mirror the Python logic, don't precompute.
- A transfer preserves the 2/5/5/3 quota ⇒ **IN and OUT must be the same position**.
- Formation legality = GK exactly 1 **and** outfield distribution is exactly one of the 7 in
  `constants.ALLOWED_FORMATION_COUNTS`.
- Country cap and budget are **stage-correct** (`constants.COUNTRY_CAP_BY_STAGE`,
  `BUDGET_BY_STAGE`); free transfers are round-correct (`FREE_TRANSFERS_BY_ROUND`).

---

## Part A — Python engine

### A1. Force-formation in `optimize.select_xi` (wc_fantasy/optimize.py:130)
Add optional `formation: str | None = None`. When `None`, keep current behavior (optimal over
the 7 via `form_vars`). When given (e.g. `"3-5-2"`): validate it's in
`C.ALLOWED_FORMATION_COUNTS`, then replace the `form_vars` selection with fixed per-position
equality constraints (`cnt == C.ALLOWED_FORMATION_COUNTS[formation][pos]`, GK==1). Captain/bench
logic unchanged.

### A2. Per-player swaps (new in optimize.py)
```python
@dataclass
class SwapCandidate:
    out_id: int; in_id: int
    hv_out: float; hv_in: float; hv_gain: float        # horizon-value delta
    price_delta: float; net_gain: float                # gain minus the -3 hit if it's an extra transfer
    reason: str                                         # short feasibility note

def best_replacements(players, horizon, squad_ids, out_id, *, budget, country_cap,
                      is_extra_transfer=False, top_n=5) -> list[SwapCandidate]:
    # bank = budget - sum(price of squad). Candidate pool: same position as out, status=='playing',
    # not in squad, price <= out.price + bank, AND country count(squad minus out) < country_cap.
    # Rank by horizon[in] desc; hv_gain = hv_in - hv_out; net = hv_gain - (3 if is_extra_transfer else 0).

def drops_for_target(players, horizon, squad_ids, in_id, *, budget, country_cap,
                     top_n=5) -> list[SwapCandidate]:
    # Direction 2: to bring in target `in_id`, the droppable squad players are the SAME position.
    # Feasible if sum(price) - out.price + in.price <= budget AND country cap still satisfied.
    # Rank by hv_gain = hv_in - hv_out desc (best = drop your weakest same-position player).
```
Reuse the country-counting pattern from `_solve_squad` (optimize.py:36-40).

### A3. CLI (wc_fantasy/main.py)
- `recommend`/`pick`: add `--formation 3-5-2` → pass through to `select_xi(..., formation=...)`.
- New `swap` command: `--out <id|name>` → `best_replacements`; `--in <id|name>` → `drops_for_target`
  (operates on the saved squad from `_current_squad_ids`; budget/cap/free from `Context`/`constants`).
  Add a `--top N` option. Add name→id resolution (reuse the fuzzy match style in `extract._norm`).
- `report.render_swaps(gd, candidates, ...)` → markdown table (OUT → IN, £Δ, HV gain, net).

### A4. Tests (tests/test_optimize.py)
`select_xi(squad, xpts, formation="3-5-2")` returns exactly 3-5-2; `best_replacements` candidates
are all same-position / affordable / within cap and sorted by hv desc; `drops_for_target` returns
same-position squad members and respects budget/cap.

---

## Part B — `data.json` rule fields (wc_fantasy/report.py:143, `save_web_json`)
Add top-level fields so the static GUI single-sources the rules (no JS hardcoding):
```python
"formations":   C.ALLOWED_FORMATION_COUNTS,
"squad_quota":  C.SQUAD_QUOTA,
"budget":       C.BUDGET_BY_STAGE.get(gd.current_stage, 100.0),
"country_cap":  C.COUNTRY_CAP_BY_STAGE.get(gd.current_stage, 3),
"free_transfers": C.FREE_TRANSFERS_BY_ROUND.get(gd.current_round_id, C.FREE_TRANSFERS_DEFAULT),
"transfer_hit": abs(C.TRANSFER_HIT_PTS),
```
(`players[]` already carries id/name/country/squad_id/position/price/ownership/status/
match_status/next_xpts/horizon_value/p_start — enough for all client-side logic.)

---

## Part C — Web GUI (docs/app.js, docs/index.html, docs/style.css)

**Add a small client-side "squad engine" in app.js** (pure helpers that mirror Part A so GUI ==
engine behavior):
- `FORMATIONS = gameData.formations` (from data.json).
- `bestXIForFormation(squad15, formationName)` → `{xi, captain, points}`: 1 GK by `next_xpts` +
  top DEF/MID/FWD by `next_xpts` for that formation's counts; captain = max `next_xpts` starter.
- `optimalXI(squad15)` → best over all 7 formations (replaces the buggy greedy in `generateLineup`).
- `validateFormation(xiIds)` → formation string, or `null` if not one of the 7.
- `bank(squadIds)` = `gameData.budget − Σ price`; `countryOK(cand, squadIds, excludeId)` using
  `gameData.country_cap`.
- `bestReplacements(outPlayer, squadIds)` and `dropsForTarget(inPlayer, squadIds)` — JS mirrors of A2.

### C1. Formations (Tab `#tab-pitch`)
- index.html: add a **Formation `<select>`** in the Tactical Summary sidebar (next to
  `#stat-formation`) + an **"Auto / Optimal"** button.
- app.js: populate the select from `gameData.formations`; default to the optimal formation; on
  change → `bestXIForFormation(squad15, chosen)` → re-render pitch + stats. `generateLineup` keeps
  the preference biases for picking the **15**, but XI selection now uses `optimalXI` (legal only).

### C2. Interactive Playing XI (Tab `#tab-pitch`)
- App state: `currentXI` (Set of 11 ids), `captainId`, `formation`.
- Make pitch cards (`renderPitchRow`) and bench items clickable: click a bench player → swap into
  XI; if the new outfield distribution passes `validateFormation`, apply; else reject with a small
  toast ("illegal formation — also move a DEF/MID/FWD"). Add a captain toggle (click crown) and the
  "Auto/Optimal" reset. Re-render formation / expected points / cost / country limits on every change.

### C3. Real per-player transfers (Tab `#tab-transfers` + Tab `#tab-players`)
Replace `renderTransferTab`'s mock with real logic:
- **Direction 1 (`#tab-transfers`):** list your 15; click one → panel of top-5 `bestReplacements`
  (IN name/country/price Δ/HV gain/net after −3) with an **Apply swap** button that mutates the
  squad and re-runs `optimalXI`.
- **Direction 2 (`#tab-players`):** make `player-database-body` rows clickable → `dropsForTarget`
  panel (which same-position squad player to drop), with Apply.
- **Real marginal table:** greedily rank the 15 by best-replacement net gain; show cumulative gain
  for k=0..K with −3 per extra beyond `gameData.free_transfers`; mark best. (Note in the UI this is
  an interactive *approximation*; the exact multi-transfer optimum is the CLI `transfer`/`swap`.)
- index.html: rework `#tab-transfers` to a squad list + replacement panel (keep the marginal table,
  now real); add click affordance to `#player-database-body` rows. style.css: selected/clickable
  states, the transfer/replacement panel, toast.

---

## Files to modify
`wc_fantasy/optimize.py` (A1, A2), `wc_fantasy/main.py` (A3 CLI), `wc_fantasy/report.py`
(A3 render_swaps, B fields), `tests/test_optimize.py` (A4); `docs/app.js`, `docs/index.html`,
`docs/style.css` (Part C).

## Verification
- **Engine:** `py -m pytest -q` green; `py -m wc_fantasy.main recommend --formation 3-5-2` forces
  it; `py -m wc_fantasy.main swap --out <id>` and `--in <id>` give sane swaps; `py -m wc_fantasy.main
  --offline update` writes `data.json`/`data.local.json` containing the new rule fields.
- **GUI:** `py -m wc_fantasy.main serve` → http://localhost:8000 → the formation picker changes the
  XI to a **legal** formation; bench↔XI swaps validate the formation; clicking a squad player shows
  real replacements; clicking a DB player shows real drop options; the marginal table reflects real
  gains. Confirm an illegal formation can no longer be produced.

## Notes for the implementer (Sonnet)
- Keep engine and JS logic in lock-step (same position-equality, same affordability/cap checks).
- Don't let the forced-favorite `+9999` bias leak into captain/points display (existing guard at
  app.js ~381 — preserve it; use base `next_xpts` for captain/points).
- The GUI squad is preference-biased (not the engine-optimal) — transfers operate on *that* squad.
- No new dependencies; no backend; respect the `data.local.json` (local) vs `data.json` (CI) split.
