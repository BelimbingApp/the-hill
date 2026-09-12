// the-hill floor board. Vanilla JavaScript, no framework, no build step.
//
// Inlined into the page at build time for the same reason as the CSS: a
// saved board must work from file:// with nothing running. Its own file so
// it is editable, greppable, and lintable as JavaScript rather than 400
// lines buried in an HTML string.
//
// paint(D) draws one snapshot. It is called once with embedded data on a
// saved board, and repeatedly against /api/board.json on a served one.
// Embedded when built to a file, null when served by `hill serve` -- the
// served page fetches instead, and repaints on a timer. One template either
// way, so the floor display and the saved snapshot cannot drift apart.
const EMBEDDED = /*__DATA__*/null;
const $ = s => document.querySelector(s);

function paint(D) {
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ago = iso => {
  if (!iso) return "never";
  const h = (Date.now() - Date.parse(iso)) / 36e5;
  if (h < 1) return Math.max(1, Math.round(h * 60)) + "m ago";
  if (h < 48) return Math.round(h) + "h ago";
  return Math.round(h / 24) + "d ago";
};

/* masthead + answer -------------------------------------------------------- */
const R = D.run || {}, P = D.peer || {};
// The run is the unit: one mission, one run, however many days it takes.
const runLine = !R.configured
  ? `<span class="run">no mission configured — showing whatever repositories are set up</span>`
  : (R.mission
      ? `<b>${esc(R.mission)}</b> <span class="run">· ${esc(R.board)}` +
        (R.age_hours != null
          ? ` · running ${R.age_hours < 48 ? R.age_hours + "h" : Math.round(R.age_hours / 24) + "d"}`
          : "") +
        (R.owners && R.owners.length ? ` · owners ${esc(R.owners.join(", "))}` : "") +
        (R.state === "closed" ? " · run closed" : " · runs until the mission is done") +
        `</span>`
      : `<span class="run">${esc(R.board)} — ${esc(R.why || "unreadable")}</span>`);
$("#mission").innerHTML = runLine +
  (P.host ? ` <span class="run">· peer <b>${esc(P.host)}</b>` +
            (P.agents && P.agents.length ? ` (${P.agents.map(esc).join(", ")})` : "") +
            `</span>` : "");

// Reads that failed this pass. A board showing fewer lanes because it could
// not reach a repository must say so; silently smaller numbers are the failure
// this whole page is built to avoid.
const readErrors = D.read_errors || [];
if (readErrors.length) {
  const el = $("#live");
  if (el) {
    el.className = "live bad";
    el.textContent = `${readErrors.length} read${readErrors.length === 1 ? "" : "s"} failed — numbers below are incomplete`;
    el.title = readErrors.map(e => `${e.path}: ${e.why}`).join("\n");
  }
}

const q = D.quota.github_core || {};
$("#meta").innerHTML =
  `collected <b>${esc(D.collected_at.replace("T", " ").replace("+00:00", "Z"))}</b><br>` +
  `github api <b>${q.remaining ?? "?"}/${q.limit ?? "?"}</b> · account <b>${esc(q.account || "?")}</b><br>` +
  `model quota <b>unknown</b> — not reported here`;

const openLanes = D.open_lanes.length;
const halts = D.halts.length;
const waiting = D.open_lanes.filter(l => l.waiting_on !== "landable").length;
const ans = $("#answer");
if (halts) {
  ans.classList.add("halt");
  $("#answerH").textContent = `Halted — ${halts} stop order open`;
  $("#answerP").textContent = "No lane may advance while an owner halt is in force.";
} else if (openLanes === 0) {
  $("#answerH").textContent = "All lanes clear";
  $("#answerP").textContent =
    `Nothing is waiting on anyone across ${Object.keys(D.repos).length} repositories. ` +
    `The floor is idle because the work is done, not because it is stuck.`;
} else {
  ans.classList.add("warn");
  $("#answerH").textContent = `${waiting} lane${waiting === 1 ? "" : "s"} waiting`;
  $("#answerP").textContent = D.open_lanes.map(l => `#${l.number} → ${l.waiting_on}`).join(" · ");
}

/* open lanes --------------------------------------------------------------- */
// Colour says who is holding the lane, not how bad it is: green only when the
// lane needs nobody, red when something is actually broken, amber when a named
// person has to act. "a reviewer" is amber, not red — nobody has done anything
// wrong, the lane is simply queued behind review capacity.
function laneChip(w) {
  if (w === "landable") return "clear";
  // Landable with nobody having read it is not a green state. It is the one
  // an author is most likely to merge themselves.
  if (w.startsWith("landable")) return "hold";
  if (w === "CI" || w.startsWith("gate disagrees")) return "stop";
  if (w === "CI (running)" || w === "unknown") return "idle";
  return "hold";
}
if (D.open_lanes.length) {
  document.getElementById("lanesSec").hidden = false;
  $("#laneRows").innerHTML = D.open_lanes.map(l => {
    const tags = (l.red || []).map(n => `<span class="tag red">${esc(n)}</span>`)
      .concat((l.pending || []).map(n => `<span class="tag run">${esc(n)} …</span>`)).join("");
    return `<tr>
      <td class="lane"><a href="${esc(l.url)}" target="_blank" rel="noopener"
        >${esc(l.repo)}#${l.number}</a><span class="ttl">${esc(l.title)}</span></td>
      <td class="who">${esc(l.agent || "—")}</td>
      <td><span class="chip ${laneChip(l.waiting_on)}">${esc(l.waiting_on)}</span></td>
      <td>${tags ? `<div class="tagrow">${tags}</div>` : '<span class="tag">no checks red</span>'}</td>
      <td class="num r">${l.idle_hours}h</td>
    </tr>`;
  }).join("");
  // The distinction the board got wrong on its first live lane, stated where a
  // reader meets it rather than buried in the coverage list.
  const onPeople = D.open_lanes.filter(l => l.waiting_on.startsWith("a reviewer")).length;
  $("#lanesNote").textContent = onPeople
    ? `${onPeople} of ${D.open_lanes.length} waiting on review, not on a failing test`
    : "a red review gate means unreviewed, not broken";
}

/* stats -------------------------------------------------------------------- */
const merged24 = Object.values(D.repos).reduce((a, r) => a + r.merged_24h, 0);
const dirty = D.workspaces.filter(w => w.dirty_files > 0).length;
const gb = (D.workspaces.reduce((a, w) => a + (w.size_mb || 0), 0) / 1024).toFixed(1);
const activeAgents = Object.values(D.agents).filter(a => a.merged_24h > 0 || a.accepts + a.changes > 0).length;
$("#stats").innerHTML = [
  ["Open lanes", openLanes, openLanes ? "awaiting someone" : "nothing in flight"],
  R.configured && R.started_at
    ? ["Delivered · this run", Object.values(D.repos).reduce((a, r) => a + (r.merged_run || 0), 0),
       (Object.values(D.repos).some(r => r.merged_run_truncated) ? "at least — " : "") +
       `since ${new Date(R.started_at).toISOString().slice(0, 10)}` +
       (merged24 ? ` · ${merged24} in last 24h` : "")]
    : ["Delivered · 24h", merged24, "pull requests merged"],
  ["Agents seen", Object.keys(D.agents).length, `${activeAgents} with recent activity`],
  ["Workspaces", D.workspaces.length, `${gb} GB · ${dirty} with uncommitted work`],
].map(([k, v, n]) =>
  `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div><div class="n">${esc(n)}</div></div>`
).join("");

/* chart -------------------------------------------------------------------- */
(() => {
  const seen = Object.keys(D.daily).sort();
  if (!seen.length) return;
  // A day with no deliveries is absent from the data, not zero. Plotting only
  // the days that exist would quietly close the gap where the board was
  // stopped — which is the most important thing this chart has to show. Fill
  // the calendar so a stop is drawn as a stop.
  // ...but only back to where every repository still has complete data. Before
  // that horizon the read was truncated, and drawing those days as zero would
  // assert "no deliveries" where the honest answer is "not known".
  const SPAN = 21, last = new Date(seen[seen.length - 1] + "T00:00:00Z"), days = [];
  const floor = D.coverage_from || seen[0];
  for (let i = SPAN - 1; i >= 0; i--) {
    const d = new Date(last); d.setUTCDate(d.getUTCDate() - i);
    const iso = d.toISOString().slice(0, 10);
    if (iso >= floor) days.push(iso);
  }
  days.forEach(d => { if (!(d in D.daily)) D.daily[d] = 0; });
  const vals = days.map(d => D.daily[d]);
  const max = Math.max(...vals);
  const W = 960, H = 260, L = 44, R = 14, T = 18, B = 46;
  const iw = W - L - R, ih = H - T - B;
  const step = iw / days.length, bw = Math.min(34, step * .62);
  const y = v => T + ih - (v / max) * ih;
  const ticks = [0, Math.round(max / 2), max];
  let s = "";
  ticks.forEach(t => {
    s += `<line x1="${L}" x2="${W - R}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}"
      stroke="var(--line)" stroke-width="1"/>`;
    s += `<text x="${L - 8}" y="${(y(t) + 4).toFixed(1)}" text-anchor="end" fill="var(--faint)"
      font-family="var(--mono)" font-size="11">${t}</text>`;
  });
  // halt band: days with zero deliveries bracketed by active days
  const zero = days.filter(d => D.daily[d] === 0);
  days.forEach((d, i) => {
    const v = D.daily[d], x = L + i * step + (step - bw) / 2;
    const isZero = v === 0;
    if (isZero) {
      s += `<rect x="${(L + i * step).toFixed(1)}" y="${T}" width="${step.toFixed(1)}" height="${ih}"
        fill="var(--stop-wash)" stroke="var(--stop)" stroke-width="1" stroke-dasharray="3 3"/>`;
    } else {
      const hgt = Math.max(2, T + ih - y(v));
      s += `<rect x="${x.toFixed(1)}" y="${y(v).toFixed(1)}" width="${bw.toFixed(1)}"
        height="${hgt.toFixed(1)}" fill="var(--accent)"/>`;
      if (v === max) {
        s += `<text x="${(x + bw / 2).toFixed(1)}" y="${(y(v) - 6).toFixed(1)}" text-anchor="middle"
          fill="var(--ink)" font-family="var(--mono)" font-size="12" font-weight="600">${v}</text>`;
      }
    }
    if (i % 2 === 0 || i === days.length - 1) {
      s += `<text x="${(L + i * step + step / 2).toFixed(1)}" y="${H - B + 17}" text-anchor="middle"
        fill="var(--faint)" font-family="var(--mono)" font-size="10.5">${d.slice(5)}</text>`;
    }
  });
  // Annotate the longest unbroken gap only, and name it for what was measured —
  // no deliveries — rather than asserting a cause the collector cannot see.
  let best = {len: 0, start: 0}, run = 0;
  days.forEach((d, i) => {
    if (D.daily[d] === 0) { run++; if (run > best.len) best = {len: run, start: i - run + 1}; }
    else run = 0;
  });
  if (best.len >= 2) {
    const cx = L + best.start * step + step * best.len / 2;
    s += `<text x="${cx.toFixed(1)}" y="${H - B + 34}" text-anchor="middle" fill="var(--stop)"
      font-family="var(--display)" font-size="12" font-weight="600"
      letter-spacing=".06em">NO DELIVERIES · ${best.len} DAYS</text>`;
  }
  s += `<line x1="${L}" x2="${W - R}" y1="${T + ih}" y2="${T + ih}" stroke="var(--line-strong)" stroke-width="1"/>`;
  $("#chart").innerHTML = s;
  const total = vals.reduce((a, b) => a + b, 0);
  $("#chartNote").textContent =
    `${total} merged over ${days.length} days from ${days[0]} · peak ${max} on ${days[vals.indexOf(max)]}` +
    (zero.length ? ` · ${zero.length} days with none` : "");
})();

/* repos -------------------------------------------------------------------- */
$("#repos").innerHTML = Object.entries(D.repos).map(([k, r]) => {
  const clear = r.open === 0;
  return `<div class="repo">
    <div class="nm">${esc(k)}</div>
    <div class="row"><span class="big">${r.open}</span><span class="lbl">open</span>
      <span class="chip ${clear ? "clear" : "hold"}">${clear ? "clear" : "in flight"}</span></div>
    <div class="lbl">${r.merged_24h} merged in last 24h</div>
  </div>`;
}).join("");

/* agents ------------------------------------------------------------------- */
const agents = Object.entries(D.agents)
  .map(([n, a]) => ({ name: n, ...a, verdicts: a.accepts + a.changes }))
  .sort((x, y) => y.merged - x.merged || y.verdicts - x.verdicts);
const topMerged = Math.max(...agents.map(a => a.merged), 1);

$("#agents").innerHTML = agents.map((a, i) => `
  <tr tabindex="0" data-i="${i}" aria-selected="${i === 0}">
    <td class="who">${esc(a.name)}</td>
    <td class="num r">${a.merged}</td>
    <td class="num r">${a.merged_24h || "·"}</td>
    <td class="num r">${a.verdicts || "·"}</td>
    <td><div class="bar"><i style="width:${(a.merged / topMerged * 100).toFixed(1)}%"></i></div></td>
  </tr>`).join("");

function drill(i) {
  const a = agents[i];
  const tot = a.verdicts;
  const ap = tot ? (a.accepts / tot * 100) : 0;
  $("#panel").innerHTML = `
    <h3>${esc(a.name)}</h3>
    <div class="role">${a.merged} deliveries · ${tot} verdicts given · last seen ${ago(a.last_seen)}</div>
    <dl class="kv">
      <dt>Delivered</dt><dd>${a.merged} merged (${a.merged_24h} in last 24h)</dd>
      <dt>Reviewed</dt><dd>${a.accepts} accept · ${a.changes} changes required</dd>
      <dt>Last seen</dt><dd>${a.last_seen ? esc(a.last_seen.replace("T", " ").replace("Z", "")) : "no record"}</dd>
      <dt>Waiting on</dt><dd>nothing — no open lane assigned</dd>
      <dt>Liveness</dt><dd>unknown — not reported by any harness</dd>
      <dt>Repos</dt><dd><div class="tagrow">${(a.repos.length ? a.repos : ["—"]).map(r => `<span class="tag">${esc(r)}</span>`).join("")}</div></dd>
    </dl>
    ${tot ? `<div class="ratio">
      <div class="track"><i class="a" style="width:${ap}%"></i><i class="c" style="width:${100 - ap}%"></i></div>
      <div class="cap"><span>${a.accepts} accepted</span><span>${a.changes} sent back</span></div>
    </div>` : ""}`;
  document.querySelectorAll("#agents tr").forEach(tr =>
    tr.setAttribute("aria-selected", tr.dataset.i === String(i)));
}
$("#agents").onclick = e => {
  const tr = e.target.closest("tr"); if (tr) drill(+tr.dataset.i);
};
$("#agents").onkeydown = e => {
  if (e.key === "Enter" || e.key === " ") {
    const tr = e.target.closest("tr");
    if (tr) { e.preventDefault(); drill(+tr.dataset.i); }
  }
};
drill(0);

/* workspaces --------------------------------------------------------------- */
const ws = [...D.workspaces].sort((a, b) => (b.dirty_files - a.dirty_files) || ((b.size_mb || 0) - (a.size_mb || 0)));
$("#ws").innerHTML = ws.map(w => `
  <tr>
    <td class="path"><span class="dot ${w.dirty_files ? "d" : ""}"></span>${esc(w.path)}</td>
    <td class="path">${esc(w.branch || "—")}</td>
    <td class="num r">${w.dirty_files || "·"}</td>
    <td class="num r">${w.unpushed || "·"}</td>
    <td class="num r">${w.size_mb != null ? w.size_mb + " MB" : "?"}</td>
  </tr>`).join("");
$("#wsNote").textContent =
  `${ws.length} checkouts · ${gb} GB · ${dirty} hold uncommitted work and must not be cleaned automatically`;

/* crew: the-hill's own state ----------------------------------------------- */
(() => {
  const H = D.hill; if (!H) return;
  const byAgent = {};
  (H.live || []).forEach(r => byAgent[r.agent] = {...r});
  (H.claims || []).forEach(c => {
    byAgent[c.agent] = byAgent[c.agent] || {agent: c.agent, freshness: "no tick", age_s: null};
    (byAgent[c.agent].holds = byAgent[c.agent].holds || []).push(c.key);
  });
  const rows = Object.values(byAgent);
  if (!rows.length) return;                      // nothing local yet; stay hidden
  document.getElementById("crewSec").hidden = false;
  const pill = f => {
    const col = f === "fresh" ? "var(--run)" : f === "stale" ? "var(--stop)" : "var(--attn)";
    return `<span class="chip" style="color:${col};background:var(--surface)">${esc(f)}</span>`;
  };
  $("#crew").innerHTML = rows.map(r => `
    <tr style="cursor:default">
      <td class="who">${esc(r.agent)}</td>
      <td>${pill(r.freshness || "no tick")}</td>
      <td class="num r">${r.age_s == null ? "—" : r.age_s + "s"}</td>
      <td class="path">${(r.holds || []).map(esc).join(", ") || "—"}</td>
      <td class="num r">${(H.unread_by_agent || {})[r.agent] || "·"}</td>
    </tr>`).join("");
  $("#crewNote").textContent =
    `${(H.claims || []).length} lane(s) claimed · ${(H.live || []).length} agent(s) ticked · `
    + `a stale tick means unknown, not stopped`;
})();

/* time and usage ----------------------------------------------------------- */
(() => {
  const U = D.usage, L = D.lead_time;
  if (!U) return;
  const compact = n => n >= 1e9 ? (n/1e9).toFixed(1)+"B" : n >= 1e6 ? (n/1e6).toFixed(1)+"M"
    : n >= 1e3 ? (n/1e3).toFixed(1)+"k" : String(n);
  const money = v => "$" + v.toLocaleString("en-US", {maximumFractionDigits: 0});

  $("#useNote").textContent =
    `${U.source} · last ${U.window_days} days · ${D.sessions.length} sessions`;

  $("#useStats").innerHTML = [
    ["Active time", U.total.active_h.toFixed(0) + "h", `across ${U.total.turns.toLocaleString()} model turns`],
    ["Output tokens", compact(U.total.tokens.output), `${compact(U.total.tokens.thinking)} of it thinking`],
    ["Cache read", compact(U.total.tokens.cache_read), `vs ${compact(U.total.tokens.cache_write)} written`],
    ["Median lead time", (L && L.median_h != null ? L.median_h + "h" : "—"),
      (L && L.p90_h != null ? `p90 ${L.p90_h}h · ${L.n} deliveries` : "opened to merged")],
  ].map(([k, v, n]) =>
    `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div><div class="n">${esc(n)}</div></div>`
  ).join("");

  // Rates are not read from any account. They are inputs, and the result is
  // labelled an estimate everywhere it appears.
  const RATES = [
    ["input", "Input", 15],
    ["output", "Output", 75],
    ["cache_write", "Cache write", 18.75],
    ["cache_read", "Cache read", 1.5],
  ];
  $("#rateNote").textContent = "US dollars per million tokens. Not read from your account — edit to match.";
  $("#rates").innerHTML = RATES.map(([k, lbl, def]) =>
    `<label for="r_${k}">${lbl}</label>
     <input id="r_${k}" type="number" min="0" step="0.25" value="${def}" data-k="${k}">`).join("");

  const rate = () => Object.fromEntries(RATES.map(([k]) =>
    [k, parseFloat(document.getElementById("r_" + k).value) || 0]));
  const costOf = (t, r) => (t.input * r.input + t.output * r.output +
    t.cache_write * r.cache_write + t.cache_read * r.cache_read) / 1e6;

  function render() {
    const r = rate();
    const rows = Object.entries(U.by_model).sort((a, b) => b[1].turns - a[1].turns);
    $("#models").innerHTML = rows.map(([m, v]) => `
      <tr style="cursor:default">
        <td class="who">${esc(m)}</td>
        <td class="num r">${v.turns.toLocaleString()}</td>
        <td class="num r">${v.active_h.toFixed(0)}h</td>
        <td class="num r">${compact(v.tokens.output)}</td>
        <td class="num r">${compact(v.tokens.cache_read)}</td>
        <td class="num r">${money(costOf(v.tokens, r))}</td>
      </tr>`).join("");
    const g = costOf(U.total.tokens, r);
    $("#modelsFoot").innerHTML = `<tr>
      <td>All models</td>
      <td class="r">${U.total.turns.toLocaleString()}</td>
      <td class="r">${U.total.active_h.toFixed(0)}h</td>
      <td class="r">${compact(U.total.tokens.output)}</td>
      <td class="r">${compact(U.total.tokens.cache_read)}</td>
      <td class="r">${money(g)}</td></tr>`;
    $("#grand").textContent = money(g);
    $("#planNote").textContent =
      `This is what the same tokens would cost billed per token on the API. A subscription ` +
      `plan does not charge that way, so treat it as the scale of the work, not an invoice. ` +
      `Covers Claude Code sessions on this machine only — ${U.not_covered}.`;
  }
  $("#rates").oninput = render;
  render();
})();
}

/* live mode ---------------------------------------------------------------- */
// A board on a wall has to say how old it is. Silence reading as "current" is
// the failure this whole tool exists to avoid, so the strip states the age of
// what is on screen and turns amber the moment a refresh stops landing.
function liveStatus(meta, error) {
  const el = $("#live");
  if (!el) return;
  if (error) {
    el.className = "live bad";
    el.textContent = `refresh failing (${error}) — showing data from ` +
      (meta && meta.collected_at ? new Date(meta.collected_at).toLocaleTimeString() : "an earlier collection");
    return;
  }
  if (meta.collecting && !meta.snapshot) {
    el.className = "live warm";
    el.textContent = "first collection running — this takes about two minutes";
    return;
  }
  const age = Math.max(0, Math.round(meta.age_seconds));
  el.className = "live" + (age > meta.interval_seconds * 2 ? " warm" : " ok");
  el.textContent = (meta.collecting ? "refreshing · " : "live · ") +
    (age < 90 ? `${age}s old` : `${Math.round(age / 60)}m old`);
}

if (EMBEDDED) {
  paint(EMBEDDED);
  const el = $("#live");
  if (el) { el.className = "live"; el.textContent = "saved snapshot — not live"; }
  // Said once, here, rather than baked into the markup: the same file is now
  // served live, and a fixed subtitle claiming "no live connection" was wrong
  // on exactly the page people will spend the most time looking at.
  $("#sub").textContent =
    "What every lane is waiting on, and since when. A saved snapshot — "
    + "it does not update. Run `hill serve` for the live board.";
} else {
  $("#sub").textContent =
    "What every lane is waiting on, and since when. Live — this page refreshes "
    + "itself and states the age of what you are reading.";
  let last = null;
  const pull = async () => {
    try {
      const r = await fetch("api/board.json", {cache: "no-store"});
      if (!r.ok) throw new Error("HTTP " + r.status);
      const j = await r.json();
      last = j;
      if (j.snapshot) paint(j.snapshot);
      liveStatus(j, null);
    } catch (e) {
      liveStatus(last, e.message || "unreachable");
    }
  };
  pull();
  setInterval(pull, 10000);
}

