"use strict";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = (v, digits = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(digits));
const fmtInt = (v) => (v === null || v === undefined ? "—" : String(v));

let DATA = null;
let ALBUMS = new Map();
let SCALE = { min: 0, max: 100 };

function scoreClass(v) {
  if (v === null || v === undefined) return "";
  const t = (v - SCALE.min) / (SCALE.max - SCALE.min);
  return t >= 0.75 ? "score-good" : t >= 0.5 ? "score-mid" : "score-bad";
}

function artUrl(a) {
  return `/api/album-art?artist=${encodeURIComponent(a.artist)}&album=${encodeURIComponent(a.album)}`;
}

function cover(a, size = "") {
  return `<img class="cover ${size}" loading="lazy" alt="" src="${artUrl(a)}" onerror="this.onerror=null;this.removeAttribute('src')">`;
}

function albumBlock(id, { size = "", extra = "" } = {}) {
  const a = ALBUMS.get(id);
  if (!a) return "";
  const year = a.release_year ? ` · ${a.release_year}` : "";
  const req = a.requester ? ` · picked by ${esc(a.requester)}` : "";
  return `<div class="album">${cover(a, size)}<div class="meta"><strong>${esc(a.album)}</strong><br><small>${esc(a.artist)}${year}${req}</small></div>${extra}</div>`;
}

function card(label, value, sub = "") {
  return `<div class="card"><div class="label">${esc(label)}</div><div class="value">${value}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

function albumList(el, ids, valueFn) {
  el.innerHTML = ids
    .map((id) => {
      const a = ALBUMS.get(id);
      return `<li>${albumBlock(id, { size: "sm", extra: `<span class="score ${scoreClass(a.mean)}">${valueFn(a)}</span>` })}</li>`;
    })
    .join("") || "<li>No albums yet.</li>";
}

// Simple table with click-to-sort headers. cols: {key, label, num?, html?(row)}
function renderTable(el, cols, rows, { sortKey = null, desc = true } = {}) {
  let state = { key: sortKey, desc };
  const draw = () => {
    const sorted = [...rows];
    if (state.key) {
      sorted.sort((a, b) => {
        const x = a[state.key], y = b[state.key];
        if (x === y) return 0;
        if (x === null || x === undefined) return 1;
        if (y === null || y === undefined) return -1;
        const cmp = typeof x === "number" ? x - y : String(x).localeCompare(String(y));
        return state.desc ? -cmp : cmp;
      });
    }
    el.innerHTML =
      `<thead><tr>${cols.map((c) => `<th data-sort="${c.key}" class="${c.num ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr></thead>` +
      `<tbody>${sorted
        .map((r) => `<tr>${cols.map((c) => `<td class="${c.num ? "num" : ""}">${c.html ? c.html(r) : c.num ? fmt(r[c.key]) : esc(r[c.key])}</td>`).join("")}</tr>`)
        .join("")}</tbody>`;
    el.querySelectorAll("th[data-sort]").forEach((th) =>
      th.addEventListener("click", () => {
        const k = th.dataset.sort;
        state = { key: k, desc: state.key === k ? !state.desc : true };
        draw();
      })
    );
  };
  draw();
}

// ---------------------------------------------------------------------------
// Charts (Plotly, themed to match light/dark)
// ---------------------------------------------------------------------------

function plotLayout(extra = {}) {
  const css = getComputedStyle(document.documentElement);
  const color = css.getPropertyValue("--pico-color").trim() || "#333";
  const grid = css.getPropertyValue("--pico-muted-border-color").trim() || "#ddd";
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color, family: "system-ui, sans-serif" },
    margin: { t: 10, r: 10, b: 50, l: 60 },
    xaxis: { gridcolor: grid, zerolinecolor: grid, ...(extra.xaxis || {}) },
    yaxis: { gridcolor: grid, zerolinecolor: grid, ...(extra.yaxis || {}) },
    ...Object.fromEntries(Object.entries(extra).filter(([k]) => k !== "xaxis" && k !== "yaxis")),
  };
}

function plot(id, traces, layout = {}) {
  if (!window.Plotly) return;
  Plotly.react(id, traces, plotLayout(layout), { displayModeBar: false, responsive: true });
}

const RYG = [[0, "#c8453b"], [0.5, "#e8c547"], [1, "#2e9d5b"]];
const GYR = [[0, "#2e9d5b"], [0.5, "#e8c547"], [1, "#c8453b"]];

function heatmap(id, rowNames, colNames, getValue, { colorscale = RYG, zmin, zmax, digits = 1 } = {}) {
  const z = rowNames.map((r) => colNames.map((c) => getValue(r, c)));
  const text = z.map((row) => row.map((v) => (v === null ? "" : Number(v).toFixed(digits))));
  plot(id, [{
    type: "heatmap", x: colNames, y: rowNames, z, text, texttemplate: "%{text}",
    colorscale, zmin, zmax, hoverongaps: false, showscale: false,
  }], { yaxis: { autorange: "reversed" }, margin: { t: 10, r: 10, b: 60, l: 90 } });
}

// ---------------------------------------------------------------------------
// Pages
// ---------------------------------------------------------------------------

function renderOverview() {
  const o = DATA.overview;
  $("#overview-cards").innerHTML = [
    card("Albums", fmtInt(o.album_count)),
    card("Reviews", fmtInt(o.review_count)),
    card("Listeners", fmtInt(o.listener_count)),
    card("Club average", fmt(o.mean), `median ${fmt(o.median)} · σ ${fmt(o.stdev)}`),
    card(`Perfect ${SCALE.max}s`, fmtInt(o.max_score_count)),
    card("Zeros", fmtInt(o.zero_score_count)),
  ].join("");
  const showcase = (ids) =>
    ids.map((id) => albumBlock(id, { size: "big", extra: `<span class="score ${scoreClass(ALBUMS.get(id).mean)}"><strong>${fmt(ALBUMS.get(id).mean)}</strong></span>` })).join("<hr>") || "—";
  $("#top-album").innerHTML = showcase(o.top_album_ids);
  $("#bottom-album").innerHTML = showcase(o.bottom_album_ids);
  plot("chart-histogram", [{ type: "histogram", x: o.scores, xbins: { start: SCALE.min, end: SCALE.max + 1, size: (SCALE.max - SCALE.min) / 20 }, marker: { color: "#4c78a8" } }], {
    xaxis: { title: "Score", range: [SCALE.min, SCALE.max + 1] }, yaxis: { title: "Reviews" }, bargap: 0.05,
  });
}

function renderAlbums() {
  const lb = DATA.leaderboards;
  albumList($("#lb-top"), lb.top, (a) => fmt(a.mean));
  albumList($("#lb-bottom"), lb.bottom, (a) => fmt(a.mean));
  albumList($("#lb-divisive"), lb.most_divisive, (a) => `σ ${fmt(a.stdev)}`);
  albumList($("#lb-unanimous"), lb.most_unanimous, (a) => `σ ${fmt(a.stdev)}`);

  const cols = [
    { key: "album", label: "Album", html: (r) => albumBlock(r.album_id, { size: "sm" }) },
    { key: "date", label: "Date" },
    { key: "mean", label: "Mean", num: true, html: (r) => `<span class="${scoreClass(r.mean)}">${fmt(r.mean)}</span>` },
    { key: "median", label: "Median", num: true },
    { key: "stdev", label: "σ", num: true },
    { key: "min", label: "Min", num: true },
    { key: "max", label: "Max", num: true },
    { key: "count", label: "Reviews", num: true, html: (r) => fmtInt(r.count) },
  ];
  const search = $("#album-search");
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const rows = DATA.albums.filter((a) => !q || [a.artist, a.album, a.requester].some((s) => (s || "").toLowerCase().includes(q)));
    renderTable($("#album-table"), cols, rows, { sortKey: "mean" });
  };
  search.oninput = draw;
  draw();

  const trackCols = [
    { key: "track", label: "Track", html: (r) => `<strong>${esc(r.track)}</strong><br><small>${esc(ALBUMS.get(r.album_id)?.album)} — ${esc(ALBUMS.get(r.album_id)?.artist)}</small>` },
    { key: "count", label: "Votes", num: true, html: (r) => `<span title="${esc(r.listeners.join(", "))}">${r.count}</span>` },
  ];
  renderTable($("#fav-tracks"), trackCols, DATA.tracks.favorite.slice(0, 50), { sortKey: "count" });
  renderTable($("#least-tracks"), trackCols, DATA.tracks.least_favorite.slice(0, 50), { sortKey: "count" });
}

function renderListenerProfile(name) {
  const p = DATA.profiles[name];
  if (!p) return;
  const albums = (ids) => ids.map((id) => albumBlock(id, { size: "sm", extra: "" })).join("") || "—";
  const others = DATA.listeners.filter((l) => l !== name);
  $("#listener-profile").innerHTML = `
    <div class="cards">
      ${card("Reviews", fmtInt(p.review_count))}
      ${card("Avg given", fmt(p.avg_given), `σ ${fmt(p.stdev_given)}`)}
      ${card("Avg received", fmt(p.avg_received), `${p.received_count} scores on ${p.requested_album_ids.length} picks, own excluded`)}
    </div>
    <div class="grid">
      <article><header>Favorite album${p.favorite_album_ids.length > 1 ? "s (tied)" : ""}</header>${albums(p.favorite_album_ids)}</article>
      <article><header>Least favorite album${p.least_favorite_album_ids.length > 1 ? "s (tied)" : ""}</header>${albums(p.least_favorite_album_ids)}</article>
    </div>
    <div class="grid">
      <article><header>Scores ${esc(name)} gave, by requester</header><div id="chart-given" class="chart"></div></article>
      <article><header>Scores ${esc(name)}'s picks got, by listener</header><div id="chart-received" class="chart"></div></article>
    </div>
    <article><header>How differently ${esc(name)} scores from each listener (RMSE)</header><div id="chart-listener-dev" class="chart"></div></article>`;
  const bar = (id, labels, values) =>
    plot(id, [{ type: "bar", x: labels, y: values, marker: { color: values.map((v) => (v === null ? "#999" : v)), colorscale: RYG, cmin: SCALE.min, cmax: SCALE.max } }], {
      yaxis: { range: [SCALE.min, SCALE.max] },
    });
  const reqs = DATA.requesters;
  bar("chart-given", reqs, reqs.map((r) => p.given_by_requester[r] ?? null));
  bar("chart-received", others, others.map((l) => p.received_by_listener[l] ?? null));
  plot("chart-listener-dev", [{ type: "bar", x: others, y: others.map((l) => p.deviation[l] ?? null), marker: { color: "#4c78a8" } }], { yaxis: { title: "RMSE", rangemode: "tozero" } });
}

function renderListeners() {
  const select = $("#listener-select");
  select.innerHTML = DATA.listeners.map((l) => `<option>${esc(l)}</option>`).join("");
  select.onchange = () => renderListenerProfile(select.value);
  if (DATA.listeners.length) renderListenerProfile(DATA.listeners[0]);

  const L = DATA.listeners;
  heatmap("chart-matrix", L, DATA.requesters, (l, r) => DATA.listener_requester[l]?.[r] ?? null, { zmin: SCALE.min, zmax: SCALE.max });
  const dev = DATA.deviation;
  const rows = [...L, "average"];
  heatmap("chart-deviation", rows, L, (a, b) => (a === "average" ? dev.average[b] : dev.matrix[a]?.[b]) ?? null, { colorscale: GYR });
}

function renderStats() {
  const s = DATA.stats;
  const sup = s.superlatives;
  const who = (x) => (x.listeners.length ? x.listeners.map(esc).join(", ") : "—");
  $("#superlatives").innerHTML = [
    card("Harshest critic", who(sup.harshest), `avg ${fmt(sup.harshest.value)}`),
    card("Most generous", who(sup.most_generous), `avg ${fmt(sup.most_generous.value)}`),
    card("Most consistent", who(sup.most_consistent), `σ ${fmt(sup.most_consistent.value)}`),
    card("Most all over the place", who(sup.most_volatile), `σ ${fmt(sup.most_volatile.value)}`),
    card("Most active", who(sup.most_active), `${fmtInt(sup.most_active.value)} reviews`),
  ].join("");

  const titles = (ids) => ids.map((id) => esc(ALBUMS.get(id)?.album)).join(", ") || "—";
  renderTable($("#requester-table"), [
    { key: "requester", label: "Requester" },
    { key: "picks", label: "Picks", num: true, html: (r) => fmtInt(r.picks) },
    { key: "avg_reception", label: "Avg reception", num: true, html: (r) => `<span class="${scoreClass(r.avg_reception)}">${fmt(r.avg_reception)}</span>` },
    { key: "best", label: "Best received", html: (r) => titles(r.best_album_ids) },
    { key: "worst", label: "Worst received", html: (r) => titles(r.worst_album_ids) },
  ], s.requesters, { sortKey: "avg_reception" });

  plot("chart-decades", [{
    type: "bar", x: s.decades.map((d) => d.decade), y: s.decades.map((d) => d.mean),
    text: s.decades.map((d) => `${d.album_count} albums`), textposition: "auto",
    marker: { color: s.decades.map((d) => d.mean), colorscale: RYG, cmin: SCALE.min, cmax: SCALE.max },
  }], { yaxis: { title: "Avg score", range: [SCALE.min, SCALE.max] } });

  const yc = s.year_correlation;
  $("#pearson").textContent = yc.pearson_r === null ? "" : `(r = ${fmt(yc.pearson_r, 2)})`;
  plot("chart-years", [{
    type: "scatter", mode: "markers",
    x: yc.points.map((p) => p.release_year), y: yc.points.map((p) => p.mean),
    text: yc.points.map((p) => `${ALBUMS.get(p.album_id)?.album} — ${ALBUMS.get(p.album_id)?.artist}`),
    hovertemplate: "%{text}<br>%{x}: %{y:.1f}<extra></extra>",
    marker: { size: 10, color: yc.points.map((p) => p.mean), colorscale: RYG, cmin: SCALE.min, cmax: SCALE.max },
  }], { xaxis: { title: "Release year" }, yaxis: { title: "Avg score" } });

  const tl = s.timeline;
  plot("chart-timeline", [
    { type: "scatter", mode: "lines", x: tl.map((t) => t.date), y: tl.map((t) => t.cumulative), line: { color: "#4c78a8" }, hoverinfo: "skip" },
    {
      type: "scatter", mode: "markers", x: tl.map((t) => t.date), y: tl.map((t) => t.cumulative),
      text: tl.map((t) => `${ALBUMS.get(t.album_id)?.album} — ${fmt(t.mean)}`), hovertemplate: "%{x}<br>%{text}<extra></extra>",
      marker: { size: 9, color: tl.map((t) => t.mean ?? SCALE.min), colorscale: RYG, cmin: SCALE.min, cmax: SCALE.max },
    },
  ], { showlegend: false, yaxis: { title: "Albums reviewed" } });

  renderTable($("#hot-takes"), [
    { key: "listener", label: "Listener" },
    { key: "album", label: "Album", html: (r) => albumBlock(r.album_id, { size: "sm" }) },
    { key: "score", label: "Score", num: true },
    { key: "club_avg", label: "Everyone else", num: true },
    { key: "delta", label: "Δ", num: true, html: (r) => `<span class="${r.delta < 0 ? "score-bad" : "score-good"}">${r.delta > 0 ? "+" : ""}${fmt(r.delta)}</span>` },
  ], s.hot_takes);
}

// ---------------------------------------------------------------------------
// Routing and boot
// ---------------------------------------------------------------------------

const PAGES = ["overview", "albums", "listeners", "stats"];
const rendered = new Set();
const RENDER = { overview: renderOverview, albums: renderAlbums, listeners: renderListeners, stats: renderStats };

function show() {
  const page = PAGES.includes(location.hash.slice(1)) ? location.hash.slice(1) : "overview";
  PAGES.forEach((p) => ($(`#${p}`).hidden = p !== page));
  document.querySelectorAll("#tabs a").forEach((a) => {
    if (a.getAttribute("href") === `#${page}`) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  if (DATA && !rendered.has(page)) {
    rendered.add(page);
    RENDER[page]();
  }
}

async function boot() {
  const status = $("#status");
  try {
    const resp = await fetch("/api/club-data");
    const body = await resp.json();
    if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
    DATA = body;
    SCALE = DATA.score_scale || SCALE;
    ALBUMS = new Map(DATA.albums.map((a) => [a.album_id, a]));
    status.remove();
    $("#footer").textContent = `${DATA.overview.album_count} albums · ${DATA.overview.review_count} reviews · data refreshes every 5 minutes`;
    show();
  } catch (err) {
    status.removeAttribute("aria-busy");
    status.textContent = `Couldn't load club data: ${err.message}`;
  }
}

window.addEventListener("hashchange", show);
window.addEventListener("DOMContentLoaded", boot);
