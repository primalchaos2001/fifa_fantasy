/* ==========================================================================
   FIFA World Cup Fantasy 2026 Engine Javascript Core
   Handle onboarding form, preference-based lineup selection, and silent logging.
   ========================================================================== */

document.addEventListener("DOMContentLoaded", () => {
  // --- App State ---
  let gameData = null;
  let userProfile = null;
  let biasedSquad = null;

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

  // ==========================================================================
  // 1. Initial Data Fetching
  // ==========================================================================
  async function init() {
    try {
      const response = await fetch("data.json");
      if (!response.ok) throw new Error("Failed to load data.json");
      gameData = await response.json();
      
      populateCountryDropdown();
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
      alert("Error initializing application. Make sure to run 'py -m wc_fantasy.main update' to compile data.json.");
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

    // 3. Select 11 players in a valid formation from the 15
    // Valid outfield positions: DEF: 3-5, MID: 2-5, FWD: 1-3. GK: exactly 1.
    // Outfield players must total 10.
    const outfieldPlayers = squad.filter(p => p.position !== "GK");
    const gkPlayers = squad.filter(p => p.position === "GK");
    
    // Sort outfield by custom xPts descending
    outfieldPlayers.sort((a, b) => b.custom_xpts - a.custom_xpts);
    
    // Pick the starting GK (highest xPts)
    const startingGK = gkPlayers[0];
    const benchGK = gkPlayers[1];

    // Greedy starting outfield XI formulation
    // Minimums: 3 DEF, 2 MID, 1 FWD
    const starters = [];
    const bench = [];

    // First force minimum structural quotas to ensure a valid formation
    const minQuota = { DEF: 3, MID: 2, FWD: 1 };
    const starterCounts = { GK: 1, DEF: 0, MID: 0, FWD: 0 };
    starters.push(startingGK);

    // Filter minimums from squad outfield
    const defs = outfieldPlayers.filter(p => p.position === "DEF");
    const mids = outfieldPlayers.filter(p => p.position === "MID");
    const fwds = outfieldPlayers.filter(p => p.position === "FWD");

    // Add minimum requirements
    for (let i = 0; i < 3; i++) { starters.push(defs[i]); starterCounts.DEF++; }
    for (let i = 0; i < 2; i++) { starters.push(mids[i]); starterCounts.MID++; }
    for (let i = 0; i < 1; i++) { starters.push(fwds[i]); starterCounts.FWD++; }

    // Remaining slots filled by the highest expected point scorers regardless of position,
    // up to formation maximums (5 DEF, 5 MID, 3 FWD).
    const maxQuota = { DEF: 5, MID: 5, FWD: 3 };
    const remainingOutfield = outfieldPlayers.filter(p => !starters.includes(p));

    for (const p of remainingOutfield) {
      if (starters.length === 11) {
        bench.push(p);
      } else if (starterCounts[p.position] < maxQuota[p.position]) {
        starters.push(p);
        starterCounts[p.position]++;
      } else {
        bench.push(p);
      }
    }
    
    // Add backup GK to bench
    bench.push(benchGK);

    // 4. Set Captain (highest next xPts in starting XI)
    // Avoid doubling the huge fake forced xPts, use the base player next_xpts for selection comparison!
    let maxBaseXpts = -1;
    let captain = null;
    starters.forEach(p => {
      if (p.next_xpts > maxBaseXpts && p.position !== "GK") {
        maxBaseXpts = p.next_xpts;
        captain = p;
      }
    });
    // Fallback to GK if no outfield
    if (!captain) captain = startingGK;

    // Bench order: Outfield by xPts desc, GK at the end
    const benchOutfield = bench.filter(p => p.position !== "GK").sort((a, b) => b.next_xpts - a.next_xpts);
    const finalBenchOrder = [...benchOutfield, benchGK];

    // Compute expected XI points
    const totalStartersXpts = starters.reduce((acc, p) => acc + p.next_xpts, 0) + captain.next_xpts; // captain doubles points

    biasedSquad = {
      manager: pref.name,
      formation: `${starterCounts.DEF}-${starterCounts.MID}-${starterCounts.FWD}`,
      starters: starters,
      bench: finalBenchOrder,
      captain: captain,
      expectedPoints: totalStartersXpts,
      spentBudget: spentBudget,
      countryCounts: countryCounts
    };
  }

  // ==========================================================================
  // 4. Render Dashboard Views
  // ==========================================================================
  function renderDashboard() {
    if (!biasedSquad) return;
    
    const sq = biasedSquad;
    
    // Update Sidebar details
    statManager.textContent = sq.manager;
    statFormation.textContent = sq.formation;
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
      el.className = "bench-item";
      
      const isFav = p.country === userProfile.country ? "favored" : "";
      const isForced = p.is_forced ? "forced-choice" : "";
      
      el.innerHTML = `
        <span class="pos-tag">${p.position}</span>
        <strong class="${isFav} ${isForced}">${p.name}</strong>
        <span style="color: var(--text-secondary)">${p.country} ($${p.price.toFixed(1)}m)</span>
        <span style="font-weight: 700; color: var(--accent-green)">${p.next_xpts.toFixed(1)} xP</span>
      `;
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
      card.className = "player-card";
      
      if (p.country === userProfile.country) card.classList.add("favored");
      if (p.is_forced) card.classList.add("forced-choice");

      const isCaptain = p.id === biasedSquad.captain.id;
      const captainEl = isCaptain ? '<div class="captain-badge">C</div>' : "";
      
      card.innerHTML = `
        ${captainEl}
        <div class="shirt-icon">
          <span class="shirt-number">${p.price.toFixed(0)}</span>
        </div>
        <div class="player-name-plate">${p.name.split(" ").slice(-1)[0]}</div>
        <div class="player-xpts-badge">${(isCaptain ? p.next_xpts * 2 : p.next_xpts).toFixed(1)} xP</div>
      `;
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
    transferMovesList.innerHTML = "";
    transferMarginalTable.innerHTML = "";

    // Highlight transfers from our current squad
    const ourSquadIds = biasedSquad.starters.map(p => p.id).concat(biasedSquad.bench.map(p => p.id));
    
    // Display transfer swap recommendations based on our newly calculated biased values
    // We pair OUT -> IN within same positions
    const outPlayers = biasedSquad.bench.slice(0, 2); // default recommendations swaps example
    const recommendedIns = gameData.players
      .filter(p => !ourSquadIds.includes(p.id) && p.status === "playing")
      .sort((a, b) => b.horizon_value - a.horizon_value);

    // Mock recommendations for GUI show based on position slots
    const mockTfs = [
      { out: biasedSquad.bench[0], in: recommendedIns.find(p => p.position === biasedSquad.bench[0].position) },
      { out: biasedSquad.bench[1], in: recommendedIns.find(p => p.position === biasedSquad.bench[1].position) }
    ].filter(tf => tf.out && tf.in);

    if (mockTfs.length === 0) {
      transferMovesList.innerHTML = '<div class="transfer-row"><div class="tf-column">No swaps recommended at this time.</div></div>';
    } else {
      mockTfs.forEach(tf => {
        const el = document.createElement("div");
        el.className = "transfer-row";
        
        el.innerHTML = `
          <div class="tf-column">
            <span class="tf-badge out">Out</span>
            <div class="tf-info">
              <span class="tf-name">${tf.out.name}</span>
              <span class="tf-meta">${tf.out.country} · HV: ${tf.out.horizon_value.toFixed(1)}</span>
            </div>
          </div>
          <div class="tf-arrow">→</div>
          <div class="tf-column">
            <span class="tf-badge in">In</span>
            <div class="tf-info">
              <span class="tf-name">${tf.in.name}</span>
              <span class="tf-meta">${tf.in.country} · HV: ${tf.in.horizon_value.toFixed(1)}</span>
            </div>
          </div>
        `;
        transferMovesList.appendChild(el);
      });
    }

    // Marginal Table
    const marginalRows = [
      { n: 0, gross: 0.0, hit: 0, net: 0.0, best: false },
      { n: 1, gross: 14.5, hit: 0, net: 14.5, best: false },
      { n: 2, gross: 26.8, hit: 0, net: 26.8, best: true },
      { n: 3, gross: 32.1, hit: 3, net: 29.1, best: false },
      { n: 4, gross: 34.0, hit: 6, net: 28.0, best: false }
    ];

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
