from pathlib import Path

src = Path("docs/index.html").read_text(encoding="utf-8")

patches = [

# ── 1. Fonts ──────────────────────────────────────────────────────────────────
(
"  <style>",
"""  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=Inter:wght@400;500&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
  <style>""",
),

# ── 2. :root + body font ──────────────────────────────────────────────────────
(
"""    :root {
      --bg:      #0d1117;
      --surface: #161b22;
      --border:  #30363d;
      --text:    #c9d1d9;
      --muted:   #8b949e;
      --dim:     #484f58;
      --blue:    #388bfd;
      --green:   #3fb950;
      --yellow:  #e3b341;
      --purple:  #bc8cff;
      --red:     #f85149;
      --orange:  #f0883e;
      --teal:    #2dd4bf;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      width: 100%; background: var(--bg); color: var(--text);
      font-family: 'Courier New', Courier, monospace;
      min-height: 100vh; display: flex; flex-direction: column;
    }""",
"""    :root {
      --bg:       #070b10;
      --surface:  #0d1117;
      --surface2: #161b22;
      --border:   #21262d;
      --border2:  #30363d;
      --text:     #e6edf3;
      --muted:    #8b949e;
      --dim:      #484f58;
      --blue:     #388bfd;
      --green:    #3fb950;
      --yellow:   #e3b341;
      --purple:   #bc8cff;
      --red:      #f85149;
      --orange:   #f0883e;
      --teal:     #2dd4bf;
      --glow-blue:   rgba(56,139,253,0.18);
      --glow-green:  rgba(63,185,80,0.18);
      --glow-yellow: rgba(227,179,65,0.18);
      --glow-purple: rgba(188,140,255,0.18);
      --glow-red:    rgba(248,81,73,0.18);
      --glow-orange: rgba(240,136,62,0.18);
      --glow-teal:   rgba(45,212,191,0.18);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    html, body {
      width: 100%; background: var(--bg); color: var(--text);
      font-family: 'Inter', -apple-system, sans-serif;
      min-height: 100vh; display: flex; flex-direction: column;
    }""",
),

# ── 3. Header ─────────────────────────────────────────────────────────────────
(
"""    /* ── Header ── */
    #header {
      padding: 12px 24px; border-bottom: 1px solid var(--border);
      display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
    }
    #header h1 { font-size: 20px; font-weight: bold; letter-spacing: 4px; }
    #era-day   { font-size: 13px; color: var(--muted); }
    #reps-display { font-size: 10px; color: var(--dim); }
    #tick-countdown { font-size: 11px; color: var(--teal); margin-left: auto; }
    #world-summary  { font-size: 11px; color: var(--dim); max-width: 320px;
                      text-align: right; font-style: italic; }""",
"""    /* ── Header ── */
    #header {
      padding: 14px 28px; border-bottom: 1px solid var(--border);
      display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
      background: linear-gradient(180deg, rgba(56,139,253,0.06) 0%, transparent 100%);
    }
    #header h1 {
      font-family: 'Rajdhani', sans-serif;
      font-size: 22px; font-weight: 700; letter-spacing: 6px;
      background: linear-gradient(135deg, #e6edf3 30%, #8b949e 100%);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
      background-clip: text;
    }
    #era-day { font-size: 12px; color: var(--muted); font-family: 'Rajdhani', sans-serif; font-weight: 500; letter-spacing: 1px; }
    #reps-display { font-size: 10px; color: var(--dim); }
    #tick-countdown { font-size: 11px; color: var(--teal); margin-left: auto; font-family: 'Space Mono', monospace; }
    #world-summary  { font-size: 11px; color: var(--dim); max-width: 320px;
                      text-align: right; font-style: italic; }""",
),

# ── 4. Panel h2 ───────────────────────────────────────────────────────────────
(
"    .panel h2 { font-size: 10px; color: var(--dim); letter-spacing: 2px; margin-bottom: 14px; }",
"""    .panel h2 {
      font-family: 'Rajdhani', sans-serif;
      font-size: 11px; font-weight: 600; color: var(--dim);
      letter-spacing: 3px; margin-bottom: 16px;
      padding-bottom: 10px; border-bottom: 1px solid var(--border);
    }""",
),

# ── 5. Landing section h2 ─────────────────────────────────────────────────────
(
"    .landing-section h2 { font-size: 10px; color: var(--dim); letter-spacing: 2px; margin-bottom: 14px; }",
"""    .landing-section h2 {
      font-family: 'Rajdhani', sans-serif;
      font-size: 11px; font-weight: 600; color: var(--muted);
      letter-spacing: 3px; margin-bottom: 16px;
      padding-bottom: 10px; border-bottom: 1px solid var(--border);
    }""",
),

# ── 6. Stat row ───────────────────────────────────────────────────────────────
(
"""    .stat-row {
      display: flex; justify-content: space-between; align-items: baseline;
      font-size: 12px; margin-bottom: 9px; gap: 8px;
    }
    .stat-label { color: var(--muted); }
    .stat-value { font-weight: bold; }""",
"""    .stat-row {
      display: flex; justify-content: space-between; align-items: baseline;
      margin-bottom: 11px; gap: 8px;
    }
    .stat-label { font-size: 11px; color: var(--dim); letter-spacing: 0.3px; }
    .stat-value { font-family: 'Space Mono', monospace; font-size: 13px; font-weight: 700; }""",
),

# ── 7. Proposal items → cards ─────────────────────────────────────────────────
(
"""    .proposal-item {
      display: flex; align-items: baseline; gap: 10px;
      padding: 9px 0; border-bottom: 1px solid #21262d; font-size: 12px;
    }
    .proposal-item:last-child { border-bottom: none; }
    .proposal-votes { color: var(--green); font-size: 11px; flex-shrink: 0; }
    .proposal-title { flex: 1; color: var(--text); }
    .proposal-link  { color: var(--blue); font-size: 10px; flex-shrink: 0; text-decoration: none; }
    .proposal-link:hover { text-decoration: underline; }
    .no-data { color: var(--dim); font-size: 11px; font-style: italic; }""",
"""    .proposal-item {
      display: flex; align-items: center; gap: 10px;
      padding: 10px 14px;
      background: var(--surface2);
      border: 1px solid var(--border);
      border-left: 3px solid var(--blue);
      border-radius: 6px;
      margin-bottom: 7px;
      font-size: 12px;
      transition: border-color 0.15s, box-shadow 0.15s, transform 0.1s;
      cursor: pointer;
    }
    .proposal-item:hover {
      border-color: var(--blue);
      box-shadow: 0 0 0 1px var(--border2), 0 4px 16px rgba(0,0,0,0.4), 0 0 20px var(--glow-blue);
      transform: translateY(-1px);
    }
    .proposal-item:last-child { margin-bottom: 0; }
    .proposal-votes {
      font-family: 'Space Mono', monospace;
      color: var(--green); font-size: 11px; flex-shrink: 0; min-width: 32px;
    }
    .proposal-title { flex: 1; color: var(--text); line-height: 1.4; }
    .proposal-link  {
      color: var(--blue); font-size: 10px; flex-shrink: 0; text-decoration: none;
      background: rgba(56,139,253,0.1); border: 1px solid rgba(56,139,253,0.3);
      padding: 3px 8px; border-radius: 4px; white-space: nowrap;
      transition: background 0.15s;
    }
    .proposal-link:hover { background: rgba(56,139,253,0.2); text-decoration: none; }
    .no-data { color: var(--dim); font-size: 11px; font-style: italic; }""",
),

# ── 8. Vote bar ───────────────────────────────────────────────────────────────
(
"""    /* ── Vote distribution bar (I) ── */
    .vote-bar-wrap {
      height: 3px; background: var(--border); border-radius: 2px;
      margin-top: 5px; overflow: hidden; display: flex;
    }
    .vote-bar-for     { background: var(--green); }
    .vote-bar-against { background: var(--red); }""",
"""    /* ── Vote distribution bar (I) ── */
    .vote-bar-wrap {
      height: 5px; background: var(--border); border-radius: 3px;
      margin-top: 8px; overflow: hidden; display: flex;
    }
    .vote-bar-for     { background: var(--green); box-shadow: 0 0 6px var(--glow-green); }
    .vote-bar-against { background: var(--red);   box-shadow: 0 0 6px var(--glow-red); }""",
),

# ── 9. Gap items → progress bars ──────────────────────────────────────────────
(
"""    /* ── Gap dashboard ── */
    .gap-item {
      font-size: 11px; padding: 5px 0; border-bottom: 1px solid #21262d;
      display: flex; justify-content: space-between; align-items: baseline;
    }
    .gap-item:last-child { border-bottom: none; }
    .gap-name { color: var(--text); flex: 1; }
    .gap-detail { color: var(--dim); font-size: 10px; text-align: right; }
    .gap-at-risk { color: var(--red); }
    .gap-need { color: var(--green); }""",
"""    /* ── Gap dashboard ── */
    .gap-item {
      padding: 10px 14px;
      background: var(--surface2); border: 1px solid var(--border);
      border-radius: 6px; margin-bottom: 7px;
    }
    .gap-item:last-child { margin-bottom: 0; }
    .gap-item-header {
      display: flex; justify-content: space-between; align-items: center;
      margin-bottom: 7px;
    }
    .gap-name { font-size: 11px; color: var(--text); font-weight: 500; }
    .gap-detail { font-size: 10px; color: var(--dim); }
    .gap-progress-track {
      height: 5px; background: var(--border); border-radius: 3px; overflow: hidden;
    }
    .gap-progress-fill { height: 100%; border-radius: 3px; transition: width 0.7s ease; }
    .gap-label-row {
      display: flex; justify-content: space-between;
      font-size: 9px; color: var(--dim); margin-top: 5px;
      font-family: 'Space Mono', monospace;
    }
    .gap-need-badge {
      font-size: 9px; font-family: 'Space Mono', monospace;
      padding: 2px 6px; border-radius: 3px; white-space: nowrap;
    }
    .gap-need   .gap-name      { color: var(--text); }
    .gap-need   .gap-need-badge { background: rgba(63,185,80,0.15); color: var(--green); }
    .gap-at-risk .gap-name     { color: var(--red); }
    .gap-at-risk .gap-need-badge { background: rgba(248,81,73,0.15); color: var(--red); }
    .gap-milestone .gap-need-badge { background: rgba(45,212,191,0.15); color: var(--teal); }""",
),

# ── 10. Panel style ───────────────────────────────────────────────────────────
(
"    .panel { padding: 18px 20px; border-right: 1px solid var(--border); }",
"""    .panel {
      padding: 20px 22px; border-right: 1px solid var(--border);
      background: linear-gradient(180deg, rgba(22,27,34,0.5) 0%, transparent 60%);
    }""",
),

# ── 11. Landing section padding ───────────────────────────────────────────────
(
"""    .landing-section {
      padding: 24px 24px; border-bottom: 1px solid var(--border);
    }""",
"""    .landing-section {
      padding: 24px 28px; border-bottom: 1px solid var(--border);
    }""",
),

# ── 12. How-card ──────────────────────────────────────────────────────────────
(
"""    .how-card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 7px; padding: 16px 18px; flex: 1; min-width: 200px;
    }
    .how-card .step { font-size: 22px; margin-bottom: 6px; }
    .how-card h3 { font-size: 12px; color: var(--text); margin-bottom: 5px; }
    .how-card p  { font-size: 11px; color: var(--muted); line-height: 1.6; }""",
"""    .how-card {
      background: var(--surface2); border: 1px solid var(--border);
      border-radius: 8px; padding: 18px 20px; flex: 1; min-width: 200px;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    .how-card:hover { border-color: var(--border2); box-shadow: 0 4px 16px rgba(0,0,0,0.3); }
    .how-card .step {
      font-family: 'Space Mono', monospace; font-size: 11px;
      color: var(--blue); margin-bottom: 8px; letter-spacing: 1px;
    }
    .how-card h3 { font-family: 'Rajdhani', sans-serif; font-size: 14px; font-weight: 600; color: var(--text); margin-bottom: 6px; }
    .how-card p  { font-size: 11px; color: var(--muted); line-height: 1.7; }""",
),

# ── 13. Milestone done badge ──────────────────────────────────────────────────
(
"    .ms-done    { background: #1a3a1a; border-color: #3fb950; color: #3fb950; }",
"    .ms-done    { background: rgba(63,185,80,0.12); border-color: #3fb950; color: #3fb950; box-shadow: 0 0 8px rgba(63,185,80,0.15); }",
),

# ── 14. Citizen rows ──────────────────────────────────────────────────────────
(
"""    .citizen-row {
      display: flex; align-items: baseline; gap: 8px;
      padding: 7px 0; border-bottom: 1px solid #21262d; font-size: 11px;
    }
    .citizen-row:last-child { border-bottom: none; }
    .citizen-rank { color: var(--dim); width: 14px; flex-shrink: 0; }
    .citizen-name { color: var(--blue); flex: 1; text-decoration: none; }
    .citizen-name:hover { text-decoration: underline; }
    .citizen-votes { color: var(--green); }
    .citizen-props { color: var(--dim); }""",
"""    .citizen-row {
      display: flex; align-items: center; gap: 8px;
      padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 11px;
    }
    .citizen-row:last-child { border-bottom: none; }
    .citizen-rank { font-family: 'Space Mono', monospace; color: var(--dim); width: 16px; flex-shrink: 0; font-size: 10px; }
    .citizen-name { color: var(--blue); flex: 1; text-decoration: none; font-weight: 500; }
    .citizen-name:hover { text-decoration: underline; color: #58a6ff; }
    .citizen-votes { font-family: 'Space Mono', monospace; color: var(--green); font-size: 10px; }
    .citizen-props { font-family: 'Space Mono', monospace; color: var(--dim); font-size: 10px; }""",
),

# ── 15. Dispatch items → cards ────────────────────────────────────────────────
(
"""    .dispatch-item { padding: 10px 0; border-bottom: 1px solid #21262d; }
    .dispatch-item:last-child { border-bottom: none; }
    .dispatch-meta { font-size: 10px; color: var(--dim); margin-bottom: 4px; }
    .dispatch-text { font-size: 11px; color: var(--muted); line-height: 1.6; }
    .dispatch-changes { font-size: 10px; color: var(--dim); margin-top: 4px; font-style: italic; }""",
"""    .dispatch-item {
      padding: 14px; background: var(--surface2);
      border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px;
    }
    .dispatch-item:last-child { margin-bottom: 0; }
    .dispatch-meta { font-family: 'Space Mono', monospace; font-size: 10px; color: var(--blue); margin-bottom: 6px; }
    .dispatch-text { font-size: 12px; color: var(--muted); line-height: 1.75; }
    .dispatch-changes { font-size: 10px; color: var(--dim); margin-top: 8px; font-style: italic; border-top: 1px solid var(--border); padding-top: 6px; }""",
),

# ── 16. Footer ────────────────────────────────────────────────────────────────
(
"""    #footer {
      padding: 10px 24px; display: flex; align-items: center; gap: 14px;
      font-size: 11px; color: var(--dim); margin-top: auto;
    }""",
"""    #footer {
      padding: 12px 28px; display: flex; align-items: center; gap: 14px;
      font-size: 11px; color: var(--dim); margin-top: auto;
      border-top: 1px solid var(--border);
      background: linear-gradient(0deg, rgba(56,139,253,0.03) 0%, transparent 100%);
      font-family: 'Space Mono', monospace;
    }""",
),

# ── 17. City container height 320 → 360 ───────────────────────────────────────
(
"""    #city-container {
      width: 100%; border-bottom: 1px solid var(--border);
      background: var(--bg); position: relative; overflow: hidden;
      flex-shrink: 0; height: 320px;
    }
    #city-bg, #city-fg { width: 100%; height: 320px; display: block; }""",
"""    #city-container {
      width: 100%; border-bottom: 1px solid var(--border);
      background: var(--bg); position: relative; overflow: hidden;
      flex-shrink: 0; height: 360px;
    }
    #city-bg, #city-fg { width: 100%; height: 360px; display: block; }""",
),

# ── 18. initCityCanvas height ─────────────────────────────────────────────────
(
"  const H = 320;\n",
"  const H = 360;\n",
),

# ── 19. Sky gradient + horizon glow ──────────────────────────────────────────
(
"""  const skyTop  = lerpColor('#1c3a5c', '#3a1a0a', pt);
  const skyBot  = lerpColor('#0d1117', '#180e0a', pt);
  const gndCol  = lerpColor('#1a2a18', '#2a1a0a', pt * 0.7);
  const gndLine = lerpColor('#3a5a30', '#4a2a10', pt);

  // Sky
  const skyGrd = ctx.createLinearGradient(0, 0, 0, GY);
  skyGrd.addColorStop(0, skyTop); skyGrd.addColorStop(1, skyBot);
  ctx.fillStyle = skyGrd; ctx.fillRect(0, 0, W, H);""",
"""  const skyTop  = lerpColor('#0d1829', '#2a1205', pt);
  const skyMid  = lerpColor('#101e36', '#1f0e04', pt);
  const skyBot  = lerpColor('#161e2e', '#220e06', pt);
  const gndCol  = lerpColor('#111a10', '#1f1005', pt * 0.7);
  const gndLine = lerpColor('#2a4022', '#3a1e08', pt);
  const horizonGlowCol = lerpColor('rgba(56,139,253,0.22)', 'rgba(240,100,40,0.28)', pt);

  // Sky — 3-stop gradient with atmosphere
  const skyGrd = ctx.createLinearGradient(0, 0, 0, GY);
  skyGrd.addColorStop(0,    skyTop);
  skyGrd.addColorStop(0.55, skyMid);
  skyGrd.addColorStop(1,    skyBot);
  ctx.fillStyle = skyGrd; ctx.fillRect(0, 0, W, H);

  // Horizon atmospheric glow
  const horizGrd = ctx.createLinearGradient(0, GY - 90, 0, GY);
  horizGrd.addColorStop(0, 'rgba(0,0,0,0)');
  horizGrd.addColorStop(1, horizonGlowCol);
  ctx.fillStyle = horizGrd; ctx.fillRect(0, GY - 90, W, 90);""",
),

# ── 20. Stars — varied sizes + colors ────────────────────────────────────────
(
"""  [[.05,.4],[.14,.15],[.24,.75],[.35,.25],[.46,.08],[.55,.65],[.64,.18],
   [.73,.72],[.83,.35],[.93,.08],[.10,.65],[.29,.55],[.42,.28],[.61,.48],[.76,.68]
  ].forEach(([fx, fy], i) => {
    const sx = ~~(fx*W), sy = ~~(fy*60+7);
    const baseAlpha = starOp * (0.5 + (i % 3) * 0.166);
    _stars.push({x: sx, y: sy, r: 1.4, phase: i * 0.74, baseAlpha});
    if (baseAlpha > 0.05) {
      ctx.globalAlpha = baseAlpha * 0.35;
      ctx.fillStyle = 'white';
      ctx.beginPath(); ctx.arc(sx, sy, 1.4, 0, Math.PI*2); ctx.fill();
    }
  });""",
"""  const STAR_DEFS = [
    [.05,.4,1.8],[.14,.15,1.2],[.24,.75,2.2],[.35,.25,1.0],[.46,.08,1.6],
    [.55,.65,1.1],[.64,.18,2.0],[.73,.72,1.4],[.83,.35,1.8],[.93,.08,1.0],
    [.10,.65,1.3],[.29,.55,1.7],[.42,.28,1.1],[.61,.48,2.4],[.76,.68,1.5],
    [.08,.22,1.0],[.19,.48,1.9],[.51,.32,1.2],[.67,.55,1.6],[.88,.18,1.3],
    [.97,.42,1.0],[.32,.70,2.0],[.44,.12,1.4],[.78,.08,1.0],[.03,.58,1.7],
  ];
  STAR_DEFS.forEach(([fx, fy, r], i) => {
    const sx = ~~(fx*W), sy = ~~(fy*70+5);
    const baseAlpha = starOp * (0.35 + (i % 4) * 0.16);
    _stars.push({x: sx, y: sy, r, phase: i * 0.74, baseAlpha});
    if (baseAlpha > 0.04) {
      ctx.globalAlpha = baseAlpha * 0.45;
      ctx.fillStyle = i % 5 === 0 ? '#e8d5b0' : i % 5 === 1 ? '#b8d4f0' : 'white';
      ctx.beginPath(); ctx.arc(sx, sy, r, 0, Math.PI*2); ctx.fill();
      if (r >= 2.0 && baseAlpha > 0.28) {
        ctx.globalAlpha = baseAlpha * 0.1;
        ctx.strokeStyle = 'white'; ctx.lineWidth = 0.5;
        ctx.beginPath(); ctx.moveTo(sx-5,sy); ctx.lineTo(sx+5,sy); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(sx,sy-5); ctx.lineTo(sx,sy+5); ctx.stroke();
      }
    }
  });""",
),

# ── 21. Moon with halo ────────────────────────────────────────────────────────
(
"""  // Moon
  if (pol < 70) {
    const mOp = Math.max(0, (70-pol)/70 * 0.85);
    const mx = ~~(W * 0.88);
    ctx.globalAlpha = mOp * 0.9; ctx.fillStyle = '#dfd8b8';
    ctx.beginPath(); ctx.arc(mx, 36, 20, 0, Math.PI*2); ctx.fill();
    ctx.globalAlpha = mOp; ctx.fillStyle = skyTop;
    ctx.beginPath(); ctx.arc(mx+12, 30, 19, 0, Math.PI*2); ctx.fill();
    ctx.globalAlpha = 1;
  }""",
"""  // Moon with halo
  if (pol < 70) {
    const mOp = Math.max(0, (70-pol)/70 * 0.9);
    const mx = ~~(W * 0.88); const my = 42;
    const haloGrd = ctx.createRadialGradient(mx, my, 18, mx, my, 52);
    haloGrd.addColorStop(0, 'rgba(220,215,185,' + (mOp * 0.14) + ')');
    haloGrd.addColorStop(1, 'rgba(0,0,0,0)');
    ctx.fillStyle = haloGrd; ctx.fillRect(mx-55, my-55, 110, 110);
    ctx.globalAlpha = mOp * 0.95; ctx.fillStyle = '#e8e0c4';
    ctx.beginPath(); ctx.arc(mx, my, 20, 0, Math.PI*2); ctx.fill();
    ctx.globalAlpha = mOp; ctx.fillStyle = skyTop;
    ctx.beginPath(); ctx.arc(mx+10, my-2, 19, 0, Math.PI*2); ctx.fill();
    ctx.globalAlpha = 1;
  }""",
),

# ── 22. Ground + fog ─────────────────────────────────────────────────────────
(
"""  // Ground
  ctx.fillStyle = gndCol; ctx.fillRect(0, GY, W, H-GY);
  ctx.strokeStyle = gndLine; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(0, GY); ctx.lineTo(W, GY); ctx.stroke();""",
"""  // Ground
  ctx.fillStyle = gndCol; ctx.fillRect(0, GY, W, H-GY);
  ctx.strokeStyle = gndLine; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.moveTo(0, GY); ctx.lineTo(W, GY); ctx.stroke();

  // Ground fog
  const fogGrd = ctx.createLinearGradient(0, GY, 0, GY + 44);
  fogGrd.addColorStop(0, 'rgba(20,40,60,0.30)');
  fogGrd.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = fogGrd; ctx.fillRect(0, GY, W, 44);""",
),

# ── 23. Building ground glow ─────────────────────────────────────────────────
(
"""      b.draw(ctx, cx, GY, state);
      ctx.restore();
      const mm = METRIC_META[m];
      if (mm) {
        ctx.globalAlpha = 0.55; ctx.fillStyle = mm.color;
        ctx.fillRect(cx, GY, b.w, 3);
        ctx.globalAlpha = 0.82; ctx.fillStyle = mm.color;
        ctx.font = '8px monospace'; ctx.textAlign = 'center';
        ctx.fillText(mm.abbr, cx + b.w/2, GY+15);
        ctx.globalAlpha = 1; ctx.textAlign = 'left';
      }""",
"""      b.draw(ctx, cx, GY, state);
      ctx.restore();
      const mm = METRIC_META[m];
      if (mm) {
        // Ground glow
        const glowGrd = ctx.createLinearGradient(cx, GY, cx, GY+38);
        glowGrd.addColorStop(0, mm.color + '30');
        glowGrd.addColorStop(1, 'rgba(0,0,0,0)');
        ctx.fillStyle = glowGrd; ctx.fillRect(cx, GY, b.w, 38);
        // Accent line
        ctx.globalAlpha = 0.7; ctx.fillStyle = mm.color;
        ctx.fillRect(cx, GY, b.w, 2);
        // Label
        ctx.globalAlpha = 0.75; ctx.fillStyle = mm.color;
        ctx.font = '8px monospace'; ctx.textAlign = 'center';
        ctx.fillText(mm.abbr, cx + b.w/2, GY+16);
        ctx.globalAlpha = 1; ctx.textAlign = 'left';
      }""",
),

# ── 24. Gap JS — progress bar rendering ───────────────────────────────────────
(
"""    const items = [];
    (gap.pending || []).forEach(p => {
      items.push(`<div class="gap-item">
        <span class="gap-name">${p.name}</span>
        <span class="gap-detail gap-need">${p.metric} ${p.current}/${p.target} <b>need +${p.gap}</b></span>
      </div>`);
    });
    (gap.at_risk || []).forEach(p => {
      items.push(`<div class="gap-item">
        <span class="gap-name gap-at-risk">${p.name} at risk</span>
        <span class="gap-detail gap-at-risk">${p.metric} ${p.current} (removal &lt; ${p.removal_at})</span>
      </div>`);
    });
    (gap.milestones_pending || []).filter(m => m.gap <= 10).forEach(m => {
      items.push(`<div class="gap-item">
        <span class="gap-name">Milestone: ${m.tag.replace(/.*\\//,'')}</span>
        <span class="gap-detail gap-need">${m.metric} ${m.current}→${m.target} <b>need ${m.gap > 0 ? '+' : ''}${m.gap}</b></span>
      </div>`);
    });""",
"""    function gapBar(current, target, cls, label) {
      const pct = Math.min(Math.round(current / Math.max(target, 1) * 100), 100);
      const color = cls === 'gap-at-risk' ? 'var(--red)' : cls === 'gap-milestone' ? 'var(--teal)' : 'var(--green)';
      return `<div class="gap-item ${cls}">
        <div class="gap-item-header">
          <span class="gap-name">${label}</span>
          <span class="gap-need-badge">+${target - current} needed</span>
        </div>
        <div class="gap-progress-track">
          <div class="gap-progress-fill" style="width:${pct}%;background:${color}"></div>
        </div>
        <div class="gap-label-row">
          <span>${current}</span>
          <span style="color:${color}">${pct}%</span>
          <span>${target}</span>
        </div>
      </div>`;
    }

    const items = [];
    (gap.pending || []).forEach(p => {
      items.push(gapBar(p.current, p.target, 'gap-need', p.name));
    });
    (gap.at_risk || []).forEach(p => {
      items.push(gapBar(Math.max(0, p.current - 5), p.current, 'gap-at-risk', p.name + ' — at risk'));
    });
    (gap.milestones_pending || []).filter(m => m.gap <= 10).forEach(m => {
      items.push(gapBar(m.current, m.target, 'gap-milestone', 'Milestone: ' + m.tag.replace(/.*\\//, '')));
    });""",
),

# ── 25. Mobile responsive city height ────────────────────────────────────────
(
"      #history-wrap { height: 160px; }\n      #radar-wrap { width: 160px; height: 160px; }",
"      #history-wrap { height: 160px; }\n      #radar-wrap { width: 160px; height: 160px; }\n      #city-container, #city-bg, #city-fg { height: 220px !important; }",
),

]

for old, new in patches:
    if old in src:
        src = src.replace(old, new, 1)
        print(f"  OK: patched {old[:60].strip()!r}")
    else:
        print(f"  MISS: {old[:60].strip()!r}")

import sys
out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("docs/preview.html")
out.write_text(src, encoding="utf-8")
print(f"\nWritten {out}")
