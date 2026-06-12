/* ==========================================================================
   FIFA World Cup Fantasy 2026 Engine Javascript Core
   Handle onboarding form, preference-based lineup selection, and silent logging.
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
  // --- App State ---
  let gameData = null;
  let userProfile = null;
  let biasedSquad = null;
  let currentSquadIds = []; // 15 player IDs
  let currentXI = new Set(); // 11 starting player IDs
  let captainId = null;
  let currentFormation = "";
  let userForcedFormation = false;
  let selectedSwapPlayerId = null;
  let selectedTransferOutPlayerId = null;

  // --- HTML Elements ---
  const splashScreen = document.getElementById("splash-screen");
  const onboardingScreen = document.getElementById("onboarding-screen");
  const loaderScreen = document.getElementById("loader-screen");
  const dashboardScreen = document.getElementById("dashboard-screen");

  const btnEnter = document.getElementById("btn-enter");
  const prefForm = document.getElementById("preferences-form");
  const favCountrySelect = document.getElementById("fav-country");

  // Tab Elements
  const tabButtons = document.querySelectorAll(".tab-btn");
  const tabPanels = document.querySelectorAll(".tab-panel");

  // Dashboard Stats
  const statManager = document.getElementById("stat-manager");
  const statFormation = document.getElementById("stat-formation");
  const statXpts = document.getElementById("stat-xpts");
  const statCost = document.getElementById("stat-cost");
  const benchPlayersList = document.getElementById("bench-players-list");
  const countryLimitsList = document.getElementById("country-limits-list");

  // Dynamic Lists
  const transferMovesList = document.getElementById("transfer-moves-list");
  const transferMarginalTable = document.getElementById("transfer-marginal-table");
  const simulatorProbBody = document.getElementById("simulator-prob-body");
  const playerDatabaseBody = document.getElementById("player-database-body");

  // Filters
  const playerSearch = document.getElementById("player-search");
  const playerFilterPos = document.getElementById("player-filter-pos");
  const playerSort = document.getElementById("player-sort");

  // Logs Profile Panel
  const logPrefName = document.getElementById("log-pref-name");
  const logPrefCountry = document.getElementById("log-pref-country");
  const logPrefLeagues = document.getElementById("log-pref-leagues");
  const logPrefClubs = document.getElementById("log-pref-clubs");
  const logPrefPlayer = document.getElementById("log-pref-player");
  const logPrefGoat = document.getElementById("log-pref-goat");
  const logSyncStatus = document.getElementById("log-sync-status");
  const logSyncUrl = document.getElementById("log-sync-url");
  const logSyncResp = document.getElementById("log-sync-resp");

  const headerRound = document.getElementById("header-round");
  const countdownTimer = document.getElementById("countdown-timer");
  const headerUpdated = document.getElementById("header-updated");

  // Modal Elements
  const playerModal = document.getElementById("player-modal");
  const modalClose = document.getElementById("modal-close");
  const modalPlayerShirt = document.getElementById("modal-player-shirt");
  const modalPlayerNumber = document.getElementById("modal-player-number");
  const modalPlayerPos = document.getElementById("modal-player-pos");
  const modalPlayerName = document.getElementById("modal-player-name");
  const modalPlayerCountry = document.getElementById("modal-player-country");
  const modalPlayerPrice = document.getElementById("modal-player-price");
  const modalPlayerOwn = document.getElementById("modal-player-own");
  const modalPlayerStatus = document.getElementById("modal-player-status");
  const modalCurrentStatus = document.getElementById("modal-current-status");
  const modalNextXpts = document.getElementById("modal-next-xpts");
  const modalHorizonVal = document.getElementById("modal-horizon-val");
  const modalFormationsGrid = document.getElementById("modal-formations-grid");
  const modalTransferTitle = document.getElementById("modal-transfer-title");
  const modalTransferSubtitle = document.getElementById("modal-transfer-subtitle");
  const modalTransferListBody = document.getElementById("modal-transfer-list-body");

  // --- Toast Notifications Helper ---
  function showToast(message, type = "error") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    const toast = document.createElement("div");
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = "0";
      setTimeout(() => { toast.remove(); }, 300);
    }, 3000);
  }

  // --- Client-side Squad Optimization Helpers ---
  function bestXIForFormation(squad15, formationName) {
    const counts = gameData.formations[formationName];
    if (!counts) return null;

    const gks = squad15.filter(p => p.position === "GK");
    const defs = squad15.filter(p => p.position === "DEF");
    const mids = squad15.filter(p => p.position === "MID");
    const fwds = squad15.filter(p => p.position === "FWD");

    // Sort each position by custom preference-biased xPts descending
    gks.sort((a, b) => b.custom_xpts - a.custom_xpts);
    defs.sort((a, b) => b.custom_xpts - a.custom_xpts);
    mids.sort((a, b) => b.custom_xpts - a.custom_xpts);
    fwds.sort((a, b) => b.custom_xpts - a.custom_xpts);

    const xi = [];
    xi.push(gks[0]);
    for (let i = 0; i < counts.DEF; i++) { if (defs[i]) xi.push(defs[i]); }
    for (let i = 0; i < counts.MID; i++) { if (mids[i]) xi.push(mids[i]); }
    for (let i = 0; i < counts.FWD; i++) { if (fwds[i]) xi.push(fwds[i]); }

    // Determine Captain: max base next_xpts in XI (excluding GK if possible)
    let maxBaseXpts = -1;
    let cap = null;
    xi.forEach(p => {
      if (p.position !== "GK" && p.next_xpts > maxBaseXpts) {
        maxBaseXpts = p.next_xpts;
        cap = p;
      }
    });
    if (!cap) cap = xi.find(p => p.position === "GK") || xi[0];

    // Compute expected points using base next_xpts, captain doubled
    let points = xi.reduce((sum, p) => sum + p.next_xpts, 0);
    if (cap) points += cap.next_xpts;

    return {
      xi: xi,
      captain: cap,
      points: points,
      formation: formationName
    };
  }

  function optimalXI(squad15) {
    let best = null;
    for (const formName in gameData.formations) {
      const res = bestXIForFormation(squad15, formName);
      if (!best || res.points > best.points) {
        best = res;
      }
    }
    return best;
  }

  function validateFormation(xiIds) {
    const playersMap = {};
    gameData.players.forEach(p => playersMap[p.id] = p);

    const counts = { GK: 0, DEF: 0, MID: 0, FWD: 0 };
    xiIds.forEach(id => {
      const p = playersMap[id];
      if (p) counts[p.position]++;
    });

    if (counts.GK !== 1) return null;
    const formStr = `${counts.DEF}-${counts.MID}-${counts.FWD}`;
    if (gameData.formations[formStr]) {
      return formStr;
    }
    return null;
  }

  function getBank(squadIds) {
    const playersMap = {};
    gameData.players.forEach(p => playersMap[p.id] = p);
    const cost = squadIds.reduce((sum, id) => sum + (playersMap[id] ? playersMap[id].price : 0), 0);
    return gameData.budget - cost;
  }

  function isCountryAllowed(cand, squadIds, excludeId = null) {
    const playersMap = {};
    gameData.players.forEach(p => playersMap[p.id] = p);

    const countries = {};
    squadIds.forEach(id => {
      if (id === excludeId) return;
      const p = playersMap[id];
      if (p) {
        countries[p.country] = (countries[p.country] || 0) + 1;
      }
    });

    return (countries[cand.country] || 0) < gameData.country_cap;
  }

  function bestReplacementsJS(outPlayer, squadIds) {
    const squadSet = new Set(squadIds);
    const bank = getBank(squadIds);

    const candidates = gameData.players.filter(p => {
      if (squadSet.has(p.id)) return false;
      if (p.position !== outPlayer.position) return false;
      if (p.status !== "playing") return false;
      if (p.price > outPlayer.price + bank) return false;
      if (!isCountryAllowed(p, squadIds, outPlayer.id)) return false;
      return true;
    });

    candidates.sort((a, b) => b.horizon_value - a.horizon_value);

    return candidates.slice(0, 5).map(p => {
      const hv_gain = p.horizon_value - outPlayer.horizon_value;
      return {
        out_id: outPlayer.id,
        in_id: p.id,
        in_player: p,
        hv_gain: hv_gain,
        price_delta: p.price - outPlayer.price,
        net_gain: hv_gain
      };
    });
  }

  function dropsForTargetJS(inPlayer, squadIds) {
    const squadSet = new Set(squadIds);
    if (squadSet.has(inPlayer.id)) return [];
    if (inPlayer.status !== "playing") return [];

    const playersMap = {};
    gameData.players.forEach(p => playersMap[p.id] = p);
    const bank = getBank(squadIds);

    const squadPlayers = squadIds.map(id => playersMap[id]).filter(p => p && p.position === inPlayer.position);

    const drops = [];
    squadPlayers.forEach(outPlayer => {
      if (inPlayer.price > outPlayer.price + bank) return;
      if (!isCountryAllowed(inPlayer, squadIds, outPlayer.id)) return;

      const hv_gain = inPlayer.horizon_value - outPlayer.horizon_value;
      drops.push({
        out_id: outPlayer.id,
        out_player: outPlayer,
        in_id: inPlayer.id,
        hv_gain: hv_gain,
        price_delta: inPlayer.price - outPlayer.price,
        net_gain: hv_gain
      });
    });

    drops.sort((a, b) => b.hv_gain - a.hv_gain);
    return drops.slice(0, 5);
  }

  // --- Modal Open/Close Controllers ---
  function openPlayerModal(player) {
    const squadSet = new Set(currentSquadIds);
    const inSquad = squadSet.has(player.id);

    modalPlayerName.textContent = player.name;
    modalPlayerNumber.textContent = player.price.toFixed(0);
    modalPlayerPos.textContent = player.position;
    modalPlayerCountry.textContent = player.country;
    modalPlayerPrice.textContent = `$${player.price.toFixed(1)}m`;
    modalPlayerOwn.textContent = `${player.ownership.toFixed(1)}% Own`;

    if (player.position === "GK") {
      modalPlayerShirt.style.background = "linear-gradient(135deg, #eab308 0%, #ca8a04 100%)";
    } else {
      modalPlayerShirt.style.background = "linear-gradient(135deg, #15803d 0%, #166534 100%)";
    }

    modalPlayerStatus.className = "modal-player-status-badge";
    if (player.status === "playing") {
      modalPlayerStatus.textContent = "Available";
      modalPlayerStatus.classList.add("status-playing");
    } else if (player.status.includes("doubt")) {
      modalPlayerStatus.textContent = "Doubtful";
      modalPlayerStatus.classList.add("status-doubt");
    } else {
      modalPlayerStatus.textContent = player.status.replace("_", " ");
      modalPlayerStatus.classList.add("status-out");
    }

    let statusText = "Not Owned";
    if (inSquad) {
      if (currentXI.has(player.id)) {
        if (captainId === player.id) {
          statusText = "Starter (C)";
        } else {
          statusText = "Starting XI";
        }
      } else {
        statusText = "Bench";
      }
    }
    modalCurrentStatus.textContent = statusText;
    modalNextXpts.textContent = player.next_xpts.toFixed(2);
    modalHorizonVal.textContent = player.horizon_value.toFixed(1);

    modalFormationsGrid.innerHTML = "";
    const squadPlayers = currentSquadIds.map(id => gameData.players.find(p => p.id === id)).filter(p => p);

    for (const formName in gameData.formations) {
      let startsInFormation = false;
      let formationPoints = 0.0;

      if (inSquad) {
        const res = bestXIForFormation(squadPlayers, formName);
        if (res) {
          startsInFormation = res.xi.some(p => p.id === player.id);
          formationPoints = res.points;
        }
      }

      const isOptimal = currentFormation === formName && !userForcedFormation;

      const el = document.createElement("div");
      el.className = `formation-row ${isOptimal ? "optimal-formation" : ""}`;
      el.innerHTML = `
        <span class="formation-name">${formName} ${isOptimal ? '<span class="text-green">★</span>' : ""}</span>
        <span class="formation-xpts">${formationPoints > 0 ? formationPoints.toFixed(1) + " xP" : "-"}</span>
        <span class="formation-status ${startsInFormation ? "status-starts" : "status-benches"}">
          ${startsInFormation ? "✔ Starts" : "➖ Bench"}
        </span>
      `;
      modalFormationsGrid.appendChild(el);
    }

    modalTransferListBody.innerHTML = "";
    if (inSquad) {
      modalTransferTitle.textContent = `Replace ${player.name.split(" ").slice(-1)[0]} (Transfer Out)`;
      modalTransferSubtitle.textContent = "Eligible same-position direct replacements from the database.";

      const reps = bestReplacementsJS(player, currentSquadIds);
      if (reps.length === 0) {
        modalTransferListBody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-secondary);padding: 20px 0;">No affordable replacement candidates found.</td></tr>';
      } else {
        reps.forEach(cand => {
          const tr = document.createElement("tr");
          const sign = cand.hv_gain >= 0 ? "+" : "";
          const colorClass = cand.hv_gain >= 0 ? "positive" : "negative";
          tr.innerHTML = `
            <td><strong>${cand.in_player.name}</strong><br><small style="color:var(--text-secondary)">${cand.in_player.country}</small></td>
            <td>$${cand.in_player.price.toFixed(1)}m</td>
            <td style="text-align: right;">${cand.in_player.horizon_value.toFixed(1)}</td>
            <td style="text-align: right;" class="net-gain ${colorClass}">
              ${sign}${cand.hv_gain.toFixed(1)}
              <button class="modal-apply-btn" style="margin-left: 8px;" onclick="applySwapAction(${player.id}, ${cand.in_player.id})">Swap</button>
            </td>
          `;
          modalTransferListBody.appendChild(tr);
        });
      }
    } else {
      modalTransferTitle.textContent = `Acquire ${player.name.split(" ").slice(-1)[0]} (Transfer In)`;
      modalTransferSubtitle.textContent = "Squad players of the same position you can sell to afford this player.";

      const drops = dropsForTargetJS(player, currentSquadIds);
      if (drops.length === 0) {
        modalTransferListBody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-secondary);padding: 20px 0;">Cannot afford this player with any same-position squad drop.</td></tr>';
      } else {
        drops.forEach(cand => {
          const tr = document.createElement("tr");
          const sign = cand.hv_gain >= 0 ? "+" : "";
          const colorClass = cand.hv_gain >= 0 ? "positive" : "negative";
          tr.innerHTML = `
            <td><strong>${cand.out_player.name}</strong><br><small style="color:var(--text-secondary)">${cand.out_player.country}</small></td>
            <td>$${cand.out_player.price.toFixed(1)}m</td>
            <td style="text-align: right;">${cand.out_player.horizon_value.toFixed(1)}</td>
            <td style="text-align: right;" class="net-gain ${colorClass}">
              ${sign}${cand.hv_gain.toFixed(1)}
              <button class="modal-apply-btn" style="margin-left: 8px;" onclick="applySwapAction(${cand.out_player.id}, ${player.id})">Swap</button>
            </td>
          `;
          modalTransferListBody.appendChild(tr);
        });
      }
    }

    playerModal.classList.add("active");
  }

  // Bind modal apply button handlers globally so onclick works
  window.applySwapAction = function(outId, inId) {
    const idx = currentSquadIds.indexOf(outId);
    if (idx !== -1) {
      currentSquadIds[idx] = inId;
      playerModal.classList.remove("active");

      // Reset transfer out player ID selection
      selectedTransferOutPlayerId = null;

      // Apply preference bias to new player if they have one
      const inPlayer = gameData.players.find(p => p.id === inId);
      if (inPlayer) {
        let customXpts = inPlayer.next_xpts;
        let isForced = false;
        
        if (inPlayer.country === userProfile.country) customXpts *= 1.25;
        if (userProfile.goat === "Messi" && inPlayer.name.includes("Messi")) customXpts *= 1.45;
        if (userProfile.goat === "Ronaldo" && (inPlayer.name.includes("Ronaldo") || inPlayer.name.includes("Cristiano"))) customXpts *= 1.45;
        if (userProfile.favoritePlayer && inPlayer.name.toLowerCase().includes(userProfile.favoritePlayer.toLowerCase())) {
          customXpts += 9999.0;
          isForced = true;
        }
        
        inPlayer.custom_xpts = customXpts;
        inPlayer.is_forced = isForced;
      }

      // Reoptimize XI
      const squadPlayers = currentSquadIds.map(id => gameData.players.find(p => p.id === id)).filter(p => p);
      
      if (userForcedFormation) {
        const res = bestXIForFormation(squadPlayers, currentFormation);
        if (res) {
          currentXI = new Set(res.xi.map(p => p.id));
          captainId = res.captain.id;
          currentFormation = res.formation;
        } else {
          const opt = optimalXI(squadPlayers);
          currentXI = new Set(opt.xi.map(p => p.id));
          captainId = opt.captain.id;
          currentFormation = opt.formation;
          userForcedFormation = false;
        }
      } else {
        const opt = optimalXI(squadPlayers);
        currentXI = new Set(opt.xi.map(p => p.id));
        captainId = opt.captain.id;
        currentFormation = opt.formation;
      }

      // Update cost and limits counts
      const countryCounts = {};
      let spentBudget = 0.0;
      squadPlayers.forEach(p => {
        countryCounts[p.country] = (countryCounts[p.country] || 0) + 1;
        spentBudget += p.price;
      });

      biasedSquad.spentBudget = spentBudget;
      biasedSquad.countryCounts = countryCounts;
      
      // Update form select selection if matching e.g. C1
      const formSelect = document.getElementById("formation-select");
      if (formSelect) formSelect.value = currentFormation;

      renderDashboard();
      showToast("Squad transfer applied successfully!", "success");
    }
  };

  // Crown captain setter
  window.setCaptain = function(pid) {
    if (!currentXI.has(pid)) {
      showToast("Only starting XI players can be captain.", "error");
      return;
    }
    const player = gameData.players.find(p => p.id === pid);
    if (player && player.position === "GK") {
      showToast("Goalkeepers cannot be captain (tactical rule).", "error");
      return;
    }
    captainId = pid;
    renderDashboard();
    showToast(`${player ? player.name : "Player"} is now Captain!`, "success");
  };

  window.openDetails = function(pid) {
    const p = gameData.players.find(x => x.id === pid);
    if (p) openPlayerModal(p);
  };

  // Close modal binding
  modalClose.addEventListener("click", () => { playerModal.classList.remove("active"); });
  playerModal.addEventListener("click", (e) => {
    if (e.target === playerModal) playerModal.classList.remove("active");
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && playerModal.classList.contains("active")) playerModal.classList.remove("active");
  });

  // Crown player card click swap handler

  window.handlePlayerCardClick = function(pid) {
    if (selectedSwapPlayerId === pid) {
      selectedSwapPlayerId = null;
      renderDashboard();
      return;
    }

    if (selectedSwapPlayerId === null) {
      selectedSwapPlayerId = pid;
      const p = gameData.players.find(x => x.id === pid);
      const inXI = currentXI.has(pid);
      showToast(`Selected ${p.name}. Click a ${inXI ? "bench" : "starting XI"} player to swap.`, "success");
      renderDashboard();
    } else {
      const p1Id = selectedSwapPlayerId;
      const p2Id = pid;

      const inXI1 = currentXI.has(p1Id);
      const inXI2 = currentXI.has(p2Id);

      if (inXI1 === inXI2) {
        selectedSwapPlayerId = pid;
        const p = gameData.players.find(x => x.id === pid);
        showToast(`Selected ${p.name}. Click a ${inXI2 ? "bench" : "starting XI"} player to swap.`, "success");
        renderDashboard();
        return;
      }

      const xiPlayerId = inXI1 ? p1Id : p2Id;
      const benchPlayerId = inXI1 ? p2Id : p1Id;

      const xiPlayer = gameData.players.find(x => x.id === xiPlayerId);
      const benchPlayer = gameData.players.find(x => x.id === benchPlayerId);

      const proposedXI = new Set(currentXI);
      proposedXI.delete(xiPlayerId);
      proposedXI.add(benchPlayerId);

      const newForm = validateFormation(proposedXI);
      if (newForm) {
        currentXI.delete(xiPlayerId);
        currentXI.add(benchPlayerId);
        currentFormation = newForm;
        userForcedFormation = true;

        const formSelect = document.getElementById("formation-select");
        if (formSelect) formSelect.value = newForm;

        if (captainId === xiPlayerId) {
          captainId = benchPlayerId;
          if (benchPlayer.position === "GK") {
            let maxBaseXpts = -1;
            let cap = null;
            proposedXI.forEach(id => {
              const pl = gameData.players.find(x => x.id === id);
              if (pl && pl.position !== "GK" && pl.next_xpts > maxBaseXpts) {
                maxBaseXpts = pl.next_xpts;
                cap = pl;
              }
            });
            captainId = cap ? cap.id : benchPlayerId;
          }
        }

        selectedSwapPlayerId = null;
        renderDashboard();
        showToast(`Subbed ${benchPlayer.name} in for ${xiPlayer.name}!`, "success");
      } else {
        const proposedCounts = { DEF: 0, MID: 0, FWD: 0 };
        proposedXI.forEach(id => {
          const pl = gameData.players.find(x => x.id === id);
          if (pl && pl.position !== "GK") proposedCounts[pl.position]++;
        });
        showToast(`Illegal formation swap! Outfield would be ${proposedCounts.DEF}-${proposedCounts.MID}-${proposedCounts.FWD}. Please select a different player to maintain a valid formation.`, "error");
        selectedSwapPlayerId = null;
        renderDashboard();
      }
    }
  };

  // ==========================================================================
  // 1. Initial Data Fetching
  // ==========================================================================
  async function init() {
    try {
      // Prefer a local-only data.local.json (written by local `update` runs, gitignored)
      // and fall back to the committed data.json (written & deployed by CI).
      let response = await fetch("data.local.json");
      if (!response.ok) response = await fetch("data.json");
      if (!response.ok) throw new Error("Failed to load data.json");
      gameData = await response.json();
      
      populateCountryDropdown();
      populateFormationDropdown();
      setupFormationListeners();
      setupCountdown();
      
      // If user profile is already cached in localStorage, we can pre-fill
      const cachedProfile = localStorage.getItem("wcf_manager_profile");
      if (cachedProfile) {
        try {
          const profile = JSON.parse(cachedProfile);
          document.getElementById("username").value = profile.name || "";
          document.getElementById("fav-country").value = profile.country || "";
          document.getElementById("fav-clubs").value = profile.clubs || "";
          document.getElementById("fav-player").value = profile.favoritePlayer || "";
          
          if (profile.leagues && Array.isArray(profile.leagues)) {
            profile.leagues.forEach(l => {
              const chk = document.querySelector(`input[name="leagues"][value="${l}"]`);
              if (chk) chk.checked = true;
            });
          }
          if (profile.goat) {
            const radio = document.querySelector(`input[name="goat"][value="${profile.goat}"]`);
            if (radio) radio.checked = true;
          }
        } catch (e) {
          console.warn("Error loading cached manager profile", e);
        }
      }
    } catch (error) {
      console.error("Initialization error:", error);
      const onFile = location.protocol === "file:";
      alert(
        onFile
          ? "This page must be served over HTTP, not opened from the file system.\n\n" +
            "Run:  py -m wc_fantasy.main serve\n\n" +
            "then open http://localhost:8000  (data.json can't be fetched from a file:// URL)."
          : "Couldn't load data.json. If it's missing, run 'py -m wc_fantasy.main update' to compile it; " +
            "otherwise check the browser console for the underlying error."
      );
    }
  }

  function populateFormationDropdown() {
    const formSelect = document.getElementById("formation-select");
    if (!formSelect || !gameData || !gameData.formations) return;
    formSelect.innerHTML = "";
    Object.keys(gameData.formations).forEach(f => {
      const opt = document.createElement("option");
      opt.value = f;
      opt.textContent = f;
      formSelect.appendChild(opt);
    });
  }

  function setupFormationListeners() {
    const formSelect = document.getElementById("formation-select");
    if (formSelect) {
      formSelect.addEventListener("change", (e) => {
        const selectedForm = e.target.value;
        const squadPlayers = currentSquadIds.map(id => gameData.players.find(p => p.id === id)).filter(p => p);
        const res = bestXIForFormation(squadPlayers, selectedForm);
        if (res) {
          currentXI = new Set(res.xi.map(p => p.id));
          captainId = res.captain.id;
          currentFormation = res.formation;
          userForcedFormation = true;
          renderDashboard();
          showToast(`Changed formation to ${selectedForm}`, "success");
        }
      });
    }

    const btnOptimalForm = document.getElementById("btn-optimal-formation");
    if (btnOptimalForm) {
      btnOptimalForm.addEventListener("click", () => {
        const squadPlayers = currentSquadIds.map(id => gameData.players.find(p => p.id === id)).filter(p => p);
        const opt = optimalXI(squadPlayers);
        if (opt) {
          currentXI = new Set(opt.xi.map(p => p.id));
          captainId = opt.captain.id;
          currentFormation = opt.formation;
          userForcedFormation = false;
          if (formSelect) formSelect.value = currentFormation;
          renderDashboard();
          showToast(`Reset to optimal formation: ${currentFormation}`, "success");
        }
      });
    }
  }

  function populateCountryDropdown() {
    if (!gameData || !gameData.teams) return;
    
    // Clear existing
    favCountrySelect.innerHTML = '<option value="" disabled selected>Select a country</option>';
    
    // Sort country names alphabetically
    const sortedCountries = Object.values(gameData.teams).sort();
    sortedCountries.forEach(country => {
      const opt = document.createElement("option");
      opt.value = country;
      opt.textContent = country;
      favCountrySelect.appendChild(opt);
    });
  }

  // Stale sources warning removed by user preference

  // Countdown timer helper
  let timerInterval = null;
  function setupCountdown() {
    if (timerInterval) clearInterval(timerInterval);
    if (!gameData || !gameData.next_deadline) {
      countdownTimer.textContent = "Locked";
      return;
    }

    const deadlineTime = new Date(gameData.next_deadline).getTime();
    
    function updateTimer() {
      const now = new Date().getTime();
      const diff = deadlineTime - now;
      
      if (diff <= 0) {
        countdownTimer.textContent = "MD Locked";
        clearInterval(timerInterval);
        return;
      }
      
      const hrs = Math.floor(diff / (1000 * 60 * 60));
      const mins = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
      const secs = Math.floor((diff % (1000 * 60)) / 1000);
      
      countdownTimer.textContent = `${hrs.toString().padStart(2, '0')}:${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
    }
    
    updateTimer();
    timerInterval = setInterval(updateTimer, 1000);
  }

  // ==========================================================================
  // 2. Navigation Flow & Forms
  // ==========================================================================
  btnEnter.addEventListener("click", () => {
    transitionPanel(splashScreen, onboardingScreen);
  });

  prefForm.addEventListener("submit", (e) => {
    e.preventDefault();
    
    // Gather form inputs
    const username = document.getElementById("username").value.trim();
    const country = document.getElementById("fav-country").value;
    const favoritePlayer = document.getElementById("fav-player").value.trim();
    
    const leagues = [];
    document.querySelectorAll('input[name="leagues"]:checked').forEach(chk => {
      leagues.push(chk.value);
    });
    
    const clubs = document.getElementById("fav-clubs").value.trim();
    const goat = document.querySelector('input[name="goat"]:checked').value;
    
    userProfile = {
      name: username,
      country: country,
      leagues: leagues,
      clubs: clubs,
      favoritePlayer: favoritePlayer,
      goat: goat
    };
    
    // Cache profile in localStorage
    localStorage.setItem("wcf_manager_profile", JSON.stringify(userProfile));

    // Move to diagnostics loader
    transitionPanel(onboardingScreen, loaderScreen);
    runDiagnostics();
  });

  function transitionPanel(fromPanel, toPanel) {
    fromPanel.style.opacity = "0";
    setTimeout(() => {
      fromPanel.classList.remove("active");
      toPanel.classList.add("active");
      setTimeout(() => {
        toPanel.style.opacity = "1";
      }, 50);
    }, 500);
  }

  // Animation logs delay simulating backend computing
  function runDiagnostics() {
    const logs = [
      document.getElementById("log-1"),
      document.getElementById("log-2"),
      document.getElementById("log-3"),
      document.getElementById("log-4"),
      document.getElementById("log-5")
    ];

    let delay = 0;
    logs.forEach((log, idx) => {
      setTimeout(() => {
        log.classList.add("active");
        
        // Final log step initiates generation
        if (idx === logs.length - 1) {
          setTimeout(() => {
            log.classList.add("success");
            generateLineup();
            renderDashboard();
            transitionPanel(loaderScreen, dashboardScreen);
            silentSubmitBackend();
          }, 800);
        } else {
          setTimeout(() => {
            log.classList.add("success");
          }, 450);
        }
      }, delay);
      delay += 600;
    });
  }

  // ==========================================================================
  // 3. Selection Optimization Engine (Custom Starting XI Generator)
  // ==========================================================================
  function generateLineup() {
    if (!gameData || !gameData.players) return;
    
    const pref = userProfile;
    const players = gameData.players;
    
    // Define favorite clubs set
    const favClubs = pref.clubs ? pref.clubs.toLowerCase().split(",").map(c => c.trim()).filter(c => c) : [];
    
    // 1. Apply Point Biases & Identify Forced Inclusions
    let biasedPlayers = players.map(p => {
      let customXpts = p.next_xpts;
      let biasNotes = [];
      let isForced = false;
      
      // Biased Country: +25% points
      if (p.country === pref.country) {
        customXpts *= 1.25;
        biasNotes.push("+25% Supported Country");
      }
      
      // GOAT candidate boost: +45% points (makes them highly likely to play)
      if (pref.goat === "Messi" && p.name.includes("Messi")) {
        customXpts *= 1.45;
        biasNotes.push("+45% G.O.A.T Choice");
      } else if (pref.goat === "Ronaldo" && (p.name.includes("Ronaldo") || p.name.includes("Cristiano"))) {
        customXpts *= 1.45;
        biasNotes.push("+45% G.O.A.T Choice");
      }
      
      // Favorite Player Match: Force-include (by adding massive points)
      if (pref.favoritePlayer && p.name.toLowerCase().includes(pref.favoritePlayer.toLowerCase())) {
        customXpts += 9999.0; // Ensures selection priority
        isForced = true;
        biasNotes.push("Forced Selection Favorite Player");
      }
      
      return {
        ...p,
        custom_xpts: customXpts,
        bias_notes: biasNotes,
        is_forced: isForced
      };
    });

    // Filter eligible players (only 'playing' status)
    eligiblePlayers = biasedPlayers.filter(p => p.status === "playing");

    // 2. Select 15-player Squad under budget & country caps (Greedy Selection)
    // Budget: $100m. Country cap: 3 players max.
    const squadQuota = { GK: 2, DEF: 5, MID: 5, FWD: 3 };
    const squad = [];
    const countryCounts = {};
    let spentBudget = 0.0;
    
    // Sort players: forced inclusions first, then by custom xPts desc
    eligiblePlayers.sort((a, b) => {
      if (a.is_forced && !b.is_forced) return -1;
      if (!a.is_forced && b.is_forced) return 1;
      return b.custom_xpts - a.custom_xpts;
    });

    // Position tracker
    const currentCounts = { GK: 0, DEF: 0, MID: 0, FWD: 0 };

    for (const p of eligiblePlayers) {
      // Check position quota
      if (currentCounts[p.position] >= squadQuota[p.position]) continue;
      
      // Check country cap (max 3 players)
      const currentCountryCount = countryCounts[p.country] || 0;
      if (currentCountryCount >= 3) continue;
      
      // Check budget constraint (leave room for remaining slots at minimum price ~3.5m each)
      const emptySlots = 15 - squad.length - 1;
      const budgetReserve = emptySlots * 3.5;
      if (spentBudget + p.price + budgetReserve > 100.0) continue;

      // Add to squad
      squad.push(p);
      currentCounts[p.position]++;
      countryCounts[p.country] = currentCountryCount + 1;
      spentBudget += p.price;
      
      if (squad.length === 15) break;
    }

    // 3. Set global squad IDs
    currentSquadIds = squad.map(p => p.id);
    
    // 4. Select starting XI and captain via optimalXI
    const opt = optimalXI(squad);
    currentXI = new Set(opt.xi.map(p => p.id));
    captainId = opt.captain.id;
    currentFormation = opt.formation;
    userForcedFormation = false;
    
    const formSelect = document.getElementById("formation-select");
    if (formSelect) formSelect.value = currentFormation;
    
    biasedSquad = {
      manager: pref.name,
      formation: currentFormation,
      starters: opt.xi,
      bench: squad.filter(p => !currentXI.has(p.id)),
      captain: opt.captain,
      expectedPoints: opt.points,
      spentBudget: spentBudget,
      countryCounts: countryCounts
    };
  }

  // ==========================================================================
  // 4. Render Dashboard Views
  // ==========================================================================
  function renderDashboard() {
    if (!gameData || !userProfile || currentSquadIds.length === 0) return;
    
    const playersMap = {};
    gameData.players.forEach(p => playersMap[p.id] = p);
    
    const starters = Array.from(currentXI).map(id => playersMap[id]).filter(p => p);
    const bench = currentSquadIds.filter(id => !currentXI.has(id)).map(id => playersMap[id]).filter(p => p);
    const cap = playersMap[captainId] || starters[0];
    
    // Recalculate expected points: sum of starters + captain's next_xpts
    let expectedPoints = starters.reduce((sum, p) => sum + p.next_xpts, 0);
    if (cap) expectedPoints += cap.next_xpts;
    
    // Spent budget
    const spentBudget = currentSquadIds.reduce((sum, id) => sum + (playersMap[id] ? playersMap[id].price : 0), 0);
    
    // Country counts
    const countryCounts = {};
    currentSquadIds.forEach(id => {
      const p = playersMap[id];
      if (p) {
        countryCounts[p.country] = (countryCounts[p.country] || 0) + 1;
      }
    });
    
    biasedSquad = {
      manager: userProfile.name,
      formation: currentFormation,
      starters: starters,
      bench: bench,
      captain: cap,
      expectedPoints: expectedPoints,
      spentBudget: spentBudget,
      countryCounts: countryCounts
    };
    
    const sq = biasedSquad;
    
    // Update Sidebar details
    statManager.textContent = sq.manager;
    statFormation.textContent = sq.formation;
    const formSelect = document.getElementById("formation-select");
    if (formSelect) formSelect.value = sq.formation;
    statXpts.textContent = sq.expectedPoints.toFixed(2);
    statCost.textContent = `$${sq.spentBudget.toFixed(1)}m / $100.0m`;

    // Render Starting XI on Football Pitch
    // Group by position
    const gks = sq.starters.filter(p => p.position === "GK");
    const defs = sq.starters.filter(p => p.position === "DEF");
    const mids = sq.starters.filter(p => p.position === "MID");
    const fwds = sq.starters.filter(p => p.position === "FWD");

    renderPitchRow(document.getElementById("grid-gk"), gks);
    renderPitchRow(document.getElementById("grid-def"), defs);
    renderPitchRow(document.getElementById("grid-mid"), mids);
    renderPitchRow(document.getElementById("grid-fwd"), fwds);

    // Render Bench substitutes
    benchPlayersList.innerHTML = "";
    sq.bench.forEach((p, idx) => {
      const el = document.createElement("div");
      el.className = `bench-item ${selectedSwapPlayerId === p.id ? "selected" : ""}`;
      
      const isFav = p.country === userProfile.country ? "favored" : "";
      const isForced = p.is_forced ? "forced-choice" : "";
      
      el.innerHTML = `
        <span class="pos-tag">${p.position}</span>
        <strong class="${isFav} ${isForced}">${p.name}</strong>
        <span style="color: var(--text-secondary)">${p.country} ($${p.price.toFixed(1)}m)</span>
        <span style="font-weight: 700; color: var(--accent-green)">${p.next_xpts.toFixed(1)} xP</span>
      `;
      el.onclick = () => handlePlayerCardClick(p.id);
      benchPlayersList.appendChild(el);
    });

    // Render Country Limits
    countryLimitsList.innerHTML = "";
    Object.entries(sq.countryCounts).forEach(([country, count]) => {
      const el = document.createElement("div");
      el.className = `limit-item ${count >= 3 ? "warning" : ""}`;
      el.innerHTML = `<span>${country}</span><strong>${count}/3</strong>`;
      countryLimitsList.appendChild(el);
    });

    // Update Headers
    headerRound.textContent = `Round ${gameData.next_round_id} (${gameData.current_stage})`;
    headerUpdated.textContent = new Date(gameData.last_updated).toLocaleTimeString();

    // Populate static recommendations / simulator tabs
    renderSimulatorTab();
    renderPlayerDatabase();
    renderPreferencesPanel();
    renderTransferTab();
  }

  function renderPitchRow(rowContainer, players) {
    rowContainer.innerHTML = "";
    players.forEach(p => {
      const card = document.createElement("div");
      card.className = `player-card ${selectedSwapPlayerId === p.id ? "selected" : ""}`;
      
      if (p.country === userProfile.country) card.classList.add("favored");
      if (p.is_forced) card.classList.add("forced-choice");

      const isCaptain = p.id === captainId;
      const captainEl = `<div class="captain-badge ${isCaptain ? 'active' : ''}" onclick="event.stopPropagation(); setCaptain(${p.id})">C</div>`;
      const infoEl = `<div class="info-badge" onclick="event.stopPropagation(); openDetails(${p.id})">i</div>`;
      
      card.innerHTML = `
        ${captainEl}
        ${infoEl}
        <div class="shirt-icon">
          <span class="shirt-number">${p.price.toFixed(0)}</span>
        </div>
        <div class="player-name-plate">${p.name.split(" ").slice(-1)[0]}</div>
        <div class="player-xpts-badge">${(isCaptain ? p.next_xpts * 2 : p.next_xpts).toFixed(1)} xP</div>
      `;
      card.onclick = () => handlePlayerCardClick(p.id);
      rowContainer.appendChild(card);
    });
  }

  function renderSimulatorTab() {
    simulatorProbBody.innerHTML = "";
    if (!gameData || !gameData.advancement) return;
    
    gameData.advancement.forEach(r => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td><strong>${r.team}</strong></td>
        <td>${r.group}</td>
        <td>${(r.p_r32 * 100).toFixed(0)}%</td>
        <td>${(r.p_r16 * 100).toFixed(0)}%</td>
        <td>${(r.p_qf * 100).toFixed(0)}%</td>
        <td>${(r.p_sf * 100).toFixed(0)}%</td>
        <td>${(r.p_final * 100).toFixed(0)}%</td>
        <td><strong class="text-green">${(r.p_win * 100).toFixed(0)}%</strong></td>
      `;
      simulatorProbBody.appendChild(tr);
    });
  }

  function renderPlayerDatabase() {
    playerDatabaseBody.innerHTML = "";
    if (!gameData || !gameData.players) return;
    
    // Apply filters
    const searchVal = playerSearch.value.toLowerCase().trim();
    const posFilter = playerFilterPos.value;
    const sortVal = playerSort.value;

    let filtered = gameData.players.filter(p => {
      const matchesSearch = p.name.toLowerCase().includes(searchVal) || p.country.toLowerCase().includes(searchVal);
      const matchesPos = posFilter === "ALL" || p.position === posFilter;
      return matchesSearch && matchesPos;
    });

    // Apply sort
    filtered.sort((a, b) => {
      if (sortVal === "xpts") return b.next_xpts - a.next_xpts;
      if (sortVal === "horizon") return b.horizon_value - a.horizon_value;
      if (sortVal === "price") return b.price - a.price;
      if (sortVal === "ownership") return b.ownership - a.ownership;
      return 0;
    });

    filtered.slice(0, 100).forEach(p => {
      const tr = document.createElement("tr");
      const isFav = p.country === userProfile.country ? "text-green" : "";
      
      tr.innerHTML = `
        <td><strong class="${isFav}">${p.name}</strong></td>
        <td>${p.position}</td>
        <td>${p.country}</td>
        <td>$${p.price.toFixed(1)}m</td>
        <td>${p.ownership.toFixed(1)}%</td>
        <td>${p.status}</td>
        <td>${p.next_xpts.toFixed(2)}</td>
        <td><strong>${p.horizon_value.toFixed(1)}</strong></td>
      `;
      tr.onclick = () => openPlayerModal(p);
      playerDatabaseBody.appendChild(tr);
    });
  }

  function renderPreferencesPanel() {
    logPrefName.textContent = userProfile.name;
    logPrefCountry.textContent = userProfile.country;
    logPrefLeagues.textContent = userProfile.leagues.join(", ") || "None";
    logPrefClubs.textContent = userProfile.clubs || "None";
    logPrefPlayer.textContent = userProfile.favoritePlayer || "None";
    logPrefGoat.textContent = userProfile.goat;
    logSyncUrl.textContent = gameData.logging_url || "Formspree URL / Google Form not configured";
  }

  function renderTransferTab() {
    const transferSquadListView = document.getElementById("transfer-squad-list-view");
    if (transferSquadListView) transferSquadListView.innerHTML = "";
    transferMovesList.innerHTML = "";
    transferMarginalTable.innerHTML = "";

    const playersMap = {};
    gameData.players.forEach(p => playersMap[p.id] = p);
    const squadPlayers = currentSquadIds.map(id => playersMap[id]).filter(p => p);

    // Populate Left Squad List
    if (transferSquadListView) {
      squadPlayers.forEach(p => {
        const row = document.createElement("div");
        row.className = `transfer-squad-row ${selectedTransferOutPlayerId === p.id ? "selected" : ""}`;
        row.innerHTML = `
          <span><strong>${p.name}</strong> (${p.position})</span>
          <span style="color: var(--text-secondary)">$${p.price.toFixed(1)}m · HV: ${p.horizon_value.toFixed(1)}</span>
        `;
        row.onclick = () => {
          selectedTransferOutPlayerId = p.id;
          renderTransferTab();
        };
        transferSquadListView.appendChild(row);
      });
    }

    // Populate Right Panel (Replacements)
    const repsTitle = document.getElementById("transfer-replacements-title");
    const repsSubtitle = document.getElementById("transfer-replacements-subtitle");

    if (selectedTransferOutPlayerId) {
      const outPlayer = playersMap[selectedTransferOutPlayerId];
      if (outPlayer) {
        if (repsTitle) repsTitle.textContent = `Direct Replacements for ${outPlayer.name.split(" ").slice(-1)[0]}`;
        if (repsSubtitle) repsSubtitle.textContent = `Eligible same-position direct replacements from the database.`;

        const reps = bestReplacementsJS(outPlayer, currentSquadIds);
        if (reps.length === 0) {
          transferMovesList.innerHTML = '<div style="color:var(--text-secondary); text-align:center; padding: 20px 0;">No affordable replacement candidates found.</div>';
        } else {
          reps.forEach(cand => {
            const el = document.createElement("div");
            el.className = "transfer-row";
            const sign = cand.hv_gain >= 0 ? "+" : "";
            const colorClass = cand.hv_gain >= 0 ? "positive" : "negative";
            el.innerHTML = `
              <div class="tf-column">
                <div class="tf-info">
                  <span class="tf-name">${cand.in_player.name}</span>
                  <span class="tf-meta">${cand.in_player.country} · Price: $${cand.in_player.price.toFixed(1)}m · HV: ${cand.in_player.horizon_value.toFixed(1)}</span>
                </div>
              </div>
              <div class="tf-column" style="align-items: flex-end; justify-content: center; flex-direction: row; gap: 8px;">
                <span class="net-gain ${colorClass}" style="font-weight: 700; margin-right: 12px; align-self: center;">${sign}${cand.hv_gain.toFixed(1)} HV</span>
                <button class="modal-apply-btn" onclick="applySwapAction(${outPlayer.id}, ${cand.in_player.id})">Swap</button>
              </div>
            `;
            transferMovesList.appendChild(el);
          });
        }
      }
    } else {
      if (repsTitle) repsTitle.textContent = "Direct Replacement Options";
      if (repsSubtitle) repsSubtitle.textContent = "Select a squad player from the left panel to list candidates.";
      transferMovesList.innerHTML = '<div style="color:var(--text-secondary); text-align:center; padding: 20px 0;">Select a squad player from the left panel to view replacements.</div>';
    }

    // Populate Marginal Table
    // For each player, find the best direct replacement gain
    const squadGains = squadPlayers.map(p => {
      const reps = bestReplacementsJS(p, currentSquadIds);
      const bestRep = reps.length > 0 ? reps[0] : null;
      const bestGain = bestRep ? bestRep.hv_gain : 0.0;
      return { player: p, bestGain: Math.max(0, bestGain) };
    });
    
    // Sort gains descending
    squadGains.sort((a, b) => b.bestGain - a.bestGain);
    
    const freeTransfers = gameData.free_transfers || 1;
    const hitPts = gameData.transfer_hit || 3;
    
    const marginalRows = [];
    let cumulativeGain = 0.0;
    
    for (let k = 0; k <= 5; k++) {
      if (k > 0) {
        cumulativeGain += squadGains[k - 1] ? squadGains[k - 1].bestGain : 0.0;
      }
      const hitsApplied = k > freeTransfers ? (k - freeTransfers) * hitPts : 0;
      const netValue = cumulativeGain - hitsApplied;
      
      marginalRows.push({
        n: k,
        gross: cumulativeGain,
        hit: hitsApplied,
        net: netValue,
        best: false
      });
    }
    
    let bestKIndex = 0;
    let maxNet = -999;
    marginalRows.forEach((row, idx) => {
      if (row.net > maxNet) {
        maxNet = row.net;
        bestKIndex = idx;
      }
    });
    
    if (maxNet > 0) {
      marginalRows[bestKIndex].best = true;
    } else {
      marginalRows[0].best = true;
    }

    marginalRows.forEach(row => {
      const tr = document.createElement("tr");
      const mark = row.best ? ' <span class="text-green" style="font-weight:700;">⬅ best</span>' : "";
      
      tr.innerHTML = `
        <td>${row.n}</td>
        <td>+${row.gross.toFixed(1)}</td>
        <td>-${row.hit}</td>
        <td><strong>+${row.net.toFixed(1)}</strong>${mark}</td>
      `;
      transferMarginalTable.appendChild(tr);
    });
  }

  // Hook filters events
  playerSearch.addEventListener("input", renderPlayerDatabase);
  playerFilterPos.addEventListener("change", renderPlayerDatabase);
  playerSort.addEventListener("change", renderPlayerDatabase);

  // Tab navigation switching logic
  tabButtons.forEach(btn => {
    btn.addEventListener("click", () => {
      // Remove active from all
      tabButtons.forEach(b => b.classList.remove("active"));
      tabPanels.forEach(p => p.classList.remove("active"));
      
      // Add active to current
      btn.classList.add("active");
      const target = document.getElementById(btn.getAttribute("data-target"));
      if (target) target.classList.add("active");
    });
  });

  // ==========================================================================
  // 5. Silent Backend logging (Google Form / Formspree Submit)
  // ==========================================================================
  async function silentSubmitBackend() {
    const loggingUrl = gameData.logging_url;
    if (!loggingUrl) {
      logSyncStatus.textContent = "Sync Skipped";
      logSyncStatus.className = "sync-badge pending";
      logSyncResp.textContent = "No logging_url configured in config.yaml. Data logged to console only.";
      console.log("[web logging] Submitted Preferences:", userProfile);
      console.log("[web logging] Generated Squad starting XI:", biasedSquad.starters.map(p => p.name));
      return;
    }

    logSyncStatus.textContent = "Syncing...";
    logSyncStatus.className = "sync-badge pending";
    
    const startersStr = biasedSquad.starters.map(p => `${p.name} (${p.position})`).join(", ");
    const squadStr = `Formation: ${biasedSquad.formation} | xPts: ${biasedSquad.expectedPoints.toFixed(1)} | Starters: ${startersStr}`;

    const isGoogleForm = loggingUrl.includes("docs.google.com/forms");

    try {
      if (isGoogleForm) {
        // Prepare Google Forms urlencoded payload
        const formData = new URLSearchParams();
        formData.append("entry.1738147737", userProfile.name);
        formData.append("entry.1397470031", userProfile.country);
        formData.append("entry.286482712", userProfile.leagues.join(", ") || "None");
        formData.append("entry.536459253", userProfile.clubs || "None");
        formData.append("entry.218699033", userProfile.favoritePlayer || "None");
        formData.append("entry.1291527402", userProfile.goat);
        formData.append("entry.1740775390", squadStr);

        // mode: 'no-cors' is required for Google Forms as it doesn't return CORS headers.
        // This sends the data successfully but returns an opaque response (status 0).
        await fetch(loggingUrl, {
          method: "POST",
          mode: "no-cors",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded"
          },
          body: formData.toString()
        });
        
        logSyncResp.textContent = "Preferences and generated squad successfully sent to Google Forms.";
      } else {
        // Standard JSON payload for custom webhooks / Formspree / API endpoints
        const payload = {
          name: userProfile.name,
          country: userProfile.country,
          leagues: userProfile.leagues,
          clubs: userProfile.clubs,
          favoritePlayer: userProfile.favoritePlayer,
          goat: userProfile.goat,
          squad: squadStr
        };

        const res = await fetch(loggingUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify(payload)
        });

        if (!res.ok) {
          throw new Error(`HTTP error ${res.status}`);
        }
        logSyncResp.textContent = "Preferences and generated squad successfully synced to custom endpoint.";
      }

      logSyncStatus.textContent = "Sync Successful";
      logSyncStatus.className = "sync-badge success";
    } catch (err) {
      logSyncStatus.textContent = "Sync Error";
      logSyncStatus.className = "sync-badge error";
      logSyncResp.textContent = `Submission failed: ${err.message}. (Ensure Google Form is public and does not require sign-in, or check CORS settings for custom endpoints.)`;
      console.warn("[web logging] Silent submission failed:", err);
    }
  }


  // Load Initial JSON Data
  init();
});
