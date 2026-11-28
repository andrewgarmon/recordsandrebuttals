"use strict";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const $ = (sel) => document.querySelector(sel);
const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const fmt = (v, digits = 1) => (v === null || v === undefined ? "—" : Number(v).toFixed(digits));
const fmtInt = (v) => (v === null || v === undefined ? "—" : String(v));

const BLANK = "data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==";
let DATA = null;
let ALBUMS = new Map();
let SCALE = { min: 0, max: 100 };
// Colors span the club's own spread (mean ± 2σ), not the whole 0-100 scale,
// so an 80 vs a 90 actually looks different.
let CMIN = 0, CMAX = 100, AXIS_MIN = 0;

function scoreClass(v) {
  if (v === null || v === undefined) return "";
  const t = (v - CMIN) / (CMAX - CMIN);
  return t >= 0.6 ? "score-good" : t >= 0.4 ? "score-mid" : "score-bad";
}

function artUrl(a) {
  return `/api/album-art?artist=${encodeURIComponent(a.artist)}&album=${encodeURIComponent(a.album)}`;
}

function cover(a, size = "") {
  return `<img class="cover ${size}" loading="lazy" alt="" src="${artUrl(a)}" onerror="this.onerror=null;this.src=BLANK">`;
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
// Genres
// ---------------------------------------------------------------------------

// Categorical palette (validated for color-vision deficiency), light and dark
// steps of the same hues. Common genres keep a fixed hue so a genre is the
// same color on every chart; "Other" is gray.
const SERIES = {
  light: ["#257de0", "#f47230", "#00b894", "#f2a600", "#ee7fac", "#008a17", "#6a4ce2", "#ec4647"],
  dark: ["#338ced", "#e76624", "#00a888", "#d28e05", "#e24d8b", "#0aac23", "#9887ef", "#eb6263"],
};
const GENRE_SLOT = { "Alternative": 0, "Rap/Hip Hop": 1, "Rock": 2, "Pop": 3, "R&B": 4, "Metal": 5, "Country": 6, "Jazz": 7 };
const isDark = () => matchMedia("(prefers-color-scheme: dark)").matches;
let GENRE_COLORS = {};

function assignGenreColors(genres) {
  const palette = isDark() ? SERIES.dark : SERIES.light;
  const used = new Set();
  GENRE_COLORS = { Other: isDark() ? "#6b6b6b" : "#a3a3a3", Unknown: isDark() ? "#6b6b6b" : "#a3a3a3" };
  const named = genres.filter((g) => g !== "Other");
  named.forEach((g) => {
    const slot = GENRE_SLOT[g];
    if (slot !== undefined && !used.has(slot)) { GENRE_COLORS[g] = palette[slot]; used.add(slot); }
  });
  named.forEach((g) => {
    if (GENRE_COLORS[g]) return;
    const slot = palette.findIndex((_, i) => !used.has(i));
    GENRE_COLORS[g] = palette[slot] ?? GENRE_COLORS.Other;
    used.add(slot);
  });
}

function genreTag(g) {
  const bucket = DATA.genres?.chart_genres.includes(g) ? g : "Other";
  return `<span class="tag"><span class="swatch" style="background:${GENRE_COLORS[bucket] || GENRE_COLORS.Other}"></span>${esc(g)}</span>`;
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
  const lo = o.scores.length ? Math.floor(Math.min(...o.scores) / 10) * 10 : SCALE.min;
  plot("chart-histogram", [{ type: "histogram", x: o.scores, xbins: { start: lo, end: SCALE.max + 1, size: (SCALE.max - SCALE.min) / 50 }, marker: { color: "#4c78a8" } }], {
    xaxis: { title: "Score", range: [lo, SCALE.max + 1] }, yaxis: { title: "Reviews" }, bargap: 0.05,
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
    { key: "genre", label: "Genre", html: (r) => (r.genre ? genreTag(r.genre) : "—") },
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
    const rows = DATA.albums.filter((a) => !q || [a.artist, a.album, a.requester, ...(a.genres || [])].some((s) => (s || "").toLowerCase().includes(q)));
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
    plot(id, [{ type: "bar", x: labels, y: values, marker: { color: values.map((v) => (v === null ? "#999" : v)), colorscale: RYG, cmin: CMIN, cmax: CMAX } }], {
      yaxis: { range: [AXIS_MIN, SCALE.max] },
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
  heatmap("chart-matrix", L, DATA.requesters, (l, r) => DATA.listener_requester[l]?.[r] ?? null, { zmin: CMIN, zmax: CMAX });
  const dev = DATA.deviation;
  const rows = [...L, "average"];
  heatmap("chart-deviation", rows, L, (a, b) => (a === "average" ? dev.average[b] : dev.matrix[a]?.[b]) ?? null, { colorscale: GYR });
}

function renderGenres() {
  const G = DATA.genres;
  if (!G) {
    $("#genres").innerHTML = "<p>Genres show up after the next data refresh.</p>";
    return;
  }
  const sup = G.superlatives;
  const names = (x) => (x.genres.length ? x.genres.map(esc).join(", ") : "—");
  $("#genre-cards").innerHTML = [
    card("Most picked", names(sup.most_picked), `${fmtInt(sup.most_picked.value)} albums`),
    card("Most loved", names(sup.most_loved), `avg ${fmt(sup.most_loved.value)}`),
    card("Least loved", names(sup.least_loved), `avg ${fmt(sup.least_loved.value)}`),
    card("Most divisive", names(sup.most_divisive), `avg σ ${fmt(sup.most_divisive.value)} per album`),
    card("Widest-ranging picker", sup.most_varied_picker.requesters.map(esc).join(", ") || "—", `${fmtInt(sup.most_varied_picker.value)} genres`),
  ].join("");

  const css = getComputedStyle(document.documentElement);
  const surface = css.getPropertyValue("--pico-card-background-color").trim() || "#fff";
  const gap = { color: surface, width: 2 };
  const cg = G.chart_genres;
  const stats = Object.fromEntries(G.genres.map((g) => [g.genre, g]));
  // "Other" as one row: totals over the folded-in genres.
  const bucketAlbums = (g) => DATA.albums.filter((a) => G.album_genre[a.album_id] === g);
  const rowFor = (g) => {
    if (stats[g]) return stats[g];
    const albums = bucketAlbums(g);
    const scores = albums.filter((a) => a.mean !== null);
    return {
      genre: g, album_count: albums.length, share: albums.length / DATA.albums.length,
      album_mean: scores.length ? scores.reduce((t, a) => t + a.mean, 0) / scores.length : null,
    };
  };
  const rows = cg.map(rowFor);

  // Ranked bars, biggest at the top. Share labels sit on the bars.
  const byCount = [...rows].sort((a, b) => a.album_count - b.album_count);
  plot("chart-genre-share", [{
    type: "bar", orientation: "h", y: byCount.map((r) => r.genre), x: byCount.map((r) => r.album_count),
    text: byCount.map((r) => `${Math.round(r.share * 100)}%`), textposition: "auto",
    marker: { color: byCount.map((r) => GENRE_COLORS[r.genre]), line: gap },
    hovertemplate: "%{y}: %{x} albums (%{text})<extra></extra>",
  }], { xaxis: { title: "Albums" }, margin: { t: 10, r: 10, b: 50, l: 100 } });

  const byScore = rows.filter((r) => r.album_mean !== null).sort((a, b) => a.album_mean - b.album_mean);
  plot("chart-genre-scores", [{
    type: "bar", orientation: "h", y: byScore.map((r) => r.genre), x: byScore.map((r) => r.album_mean),
    text: byScore.map((r) => fmt(r.album_mean)), textposition: "auto",
    marker: { color: byScore.map((r) => GENRE_COLORS[r.genre]), line: gap },
    hovertemplate: "%{y}: %{x:.1f} average album score<extra></extra>",
  }], { xaxis: { title: "Average album score", range: [AXIS_MIN, SCALE.max] }, margin: { t: 10, r: 10, b: 50, l: 100 } });

  // Taste heatmap: diverging red (below their usual) - gray - blue (above).
  const L = DATA.listeners;
  const T = G.tastes;
  const z = L.map((l) => cg.map((g) => T[l]?.affinity[g] ?? null));
  const lim = Math.max(5, ...z.flat().filter((v) => v !== null).map(Math.abs));
  const mid = isDark() ? "#383838" : "#efefef";
  plot("chart-genre-taste", [{
    type: "heatmap", x: cg, y: L, z, zmin: -lim, zmax: lim, showscale: false, hoverongaps: false,
    colorscale: [[0, "#ec4647"], [0.5, mid], [1, "#257de0"]],
    text: z.map((row) => row.map((v) => (v === null ? "" : `${v > 0 ? "+" : ""}${v.toFixed(1)}`))),
    texttemplate: "%{text}",
    customdata: L.map((l) => cg.map((g) => [fmt(T[l]?.avg[g]), T[l]?.counts[g] ?? 0, fmt(T[l]?.mean)])),
    hovertemplate: "%{y} on %{x}: avg %{customdata[0]} over %{customdata[1]} albums<br>(their usual: %{customdata[2]})<extra></extra>",
    xgap: 2, ygap: 2,
  }], { yaxis: { autorange: "reversed" }, xaxis: { side: "top" }, margin: { t: 40, r: 10, b: 10, l: 90 } });

  const list = (gs) => (gs.length ? gs.map(genreTag).join(" ") : "—");
  renderTable($("#genre-favs"), [
    { key: "listener", label: "Listener" },
    { key: "fav", label: "Favorite", html: (r) => `${list(r.fav)} <small>${r.favBy}</small>` },
    { key: "least", label: "Least favorite", html: (r) => `${list(r.least)} <small>${r.leastBy}</small>` },
  ], L.map((l) => {
    const t = T[l];
    const by = (gs) => (gs.length ? `${t.affinity[gs[0]] > 0 ? "+" : ""}${fmt(t.affinity[gs[0]])}` : "");
    return { listener: l, fav: t.favorite, least: t.least_favorite, favBy: by(t.favorite), leastBy: by(t.least_favorite) };
  }));

  // Stacked bars: one segment per genre, 2px surface gaps between them.
  const stacked = (id, cats, counts, xTitle) =>
    plot(id, cg.map((g) => ({
      type: "bar", name: g, x: cats, y: cats.map((c) => counts[c]?.[g] ?? 0),
      marker: { color: GENRE_COLORS[g], line: gap },
      hovertemplate: `%{x}: %{y} ${esc(g)}<extra></extra>`,
    })), { barmode: "stack", xaxis: { title: xTitle }, yaxis: { title: "Albums" }, legend: { orientation: "h", y: -0.25 }, margin: { t: 10, r: 10, b: 90, l: 50 } });
  stacked("chart-genre-requesters", DATA.requesters, G.by_requester, "");
  stacked("chart-genre-decades", Object.keys(G.by_decade), G.by_decade, "");

  const titles = (ids) => ids.map((id) => esc(ALBUMS.get(id)?.album)).join(", ") || "—";
  renderTable($("#genre-table"), [
    { key: "genre", label: "Genre", html: (r) => genreTag(r.genre) },
    { key: "album_count", label: "Albums", num: true, html: (r) => fmtInt(r.album_count) },
    { key: "album_mean", label: "Avg album", num: true, html: (r) => `<span class="${scoreClass(r.album_mean)}">${fmt(r.album_mean)}</span>` },
    { key: "avg_album_stdev", label: "Avg σ", num: true },
    { key: "best", label: "Best", html: (r) => titles(r.best_album_ids) },
    { key: "worst", label: "Worst", html: (r) => titles(r.worst_album_ids) },
  ], G.genres, { sortKey: "album_count" });

  const other = G.genres.filter((g) => !cg.includes(g.genre)).map((g) => g.genre);
  $("#genre-note").textContent =
    "Genres come from Deezer (iTunes when Deezer doesn't know an album), using each album's first-listed genre." +
    (other.length ? ` Other = ${other.join(", ")}.` : "");
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
    marker: { color: s.decades.map((d) => d.mean), colorscale: RYG, cmin: CMIN, cmax: CMAX },
  }], { yaxis: { title: "Avg score", range: [AXIS_MIN, SCALE.max] } });

  const yc = s.year_correlation;
  $("#pearson").textContent = yc.pearson_r === null ? "" : `(r = ${fmt(yc.pearson_r, 2)})`;
  plot("chart-years", [{
    type: "scatter", mode: "markers",
    x: yc.points.map((p) => p.release_year), y: yc.points.map((p) => p.mean),
    text: yc.points.map((p) => `${ALBUMS.get(p.album_id)?.album} — ${ALBUMS.get(p.album_id)?.artist}`),
    hovertemplate: "%{text}<br>%{x}: %{y:.1f}<extra></extra>",
    marker: { size: 10, color: yc.points.map((p) => p.mean), colorscale: RYG, cmin: CMIN, cmax: CMAX },
  }], { xaxis: { title: "Release year" }, yaxis: { title: "Avg score" } });

  const tl = s.timeline;
  plot("chart-timeline", [
    { type: "scatter", mode: "lines", x: tl.map((t) => t.date), y: tl.map((t) => t.cumulative), line: { color: "#4c78a8" }, hoverinfo: "skip" },
    {
      type: "scatter", mode: "markers", x: tl.map((t) => t.date), y: tl.map((t) => t.cumulative),
      text: tl.map((t) => `${ALBUMS.get(t.album_id)?.album} — ${fmt(t.mean)}`), hovertemplate: "%{x}<br>%{text}<extra></extra>",
      marker: { size: 9, color: tl.map((t) => t.mean ?? CMIN), colorscale: RYG, cmin: CMIN, cmax: CMAX },
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

const PAGES = ["overview", "albums", "listeners", "genres", "stats"];
const rendered = new Set();
const RENDER = { overview: renderOverview, albums: renderAlbums, listeners: renderListeners, genres: renderGenres, stats: renderStats };

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
    // Older payloads (before genres) still render; the Genres tab says so.
    DATA.genres = DATA.genres || null;
    if (DATA.genres) assignGenreColors(DATA.genres.chart_genres);
    const { mean, stdev } = DATA.overview;
    if (mean !== null && stdev) {
      CMIN = Math.max(SCALE.min, mean - 2 * stdev);
      CMAX = Math.min(SCALE.max, mean + 2 * stdev);
      AXIS_MIN = Math.max(SCALE.min, Math.floor((CMIN - 10) / 10) * 10);
    }
    status.remove();
    const updated = DATA.generated_at ? ` · updated ${new Date(DATA.generated_at).toLocaleString()}` : "";
    $("#footer").textContent = `${DATA.overview.album_count} albums · ${DATA.overview.review_count} reviews${updated}`;
    show();
  } catch (err) {
    status.removeAttribute("aria-busy");
    status.textContent = `Couldn't load club data: ${err.message}`;
  }
}

window.addEventListener("hashchange", show);
window.addEventListener("DOMContentLoaded", boot);
