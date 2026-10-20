// The game page: joining, the lobby, the trading screen of a live round, and results.
import { get, knownPlayers, loadToken, post, saveToken, useToken } from "/static/api.js";
import { botFills, setupBot, showBot } from "/static/bot.js";

const $ = (id) => document.getElementById(id);
const code = location.pathname.split("/").pop().toUpperCase();
const SEC = 1e9;
const LADDER_ROWS = 21;

let token = loadToken(code);
let me = null;
let game = null;
let ws = null;
let seat = null;      // the seat's state in the live round, kept from the gateway's messages
let roundId = null;
let seated = false;
let ladderCenter = null;
let reconnectDelay = 500;

$("code").textContent = code;
setupBot(code);
$("copy-link").onclick = () => navigator.clipboard?.writeText(`${location.origin}/g/${code}`);
$("bot-command").textContent =
  `arena-kit join my_bot.py --server ${location.origin} --code ${code} --name my-bot`;

// Joining

function showJoin() {
  $("join-panel").classList.remove("hidden");
  const known = knownPlayers(code);
  $("known").innerHTML = "";
  for (const p of known) {
    const b = document.createElement("button");
    b.textContent = `Carry on as ${p.name}`;
    b.onclick = () => { useToken(code, p.token); token = p.token; start(); };
    $("known").appendChild(b);
  }
}

$("join").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = new FormData(e.target).get("name");
  try {
    const r = await post(`/api/games/${code}/join`, { name, kind: "human" });
    saveToken(code, r.token, r.player.name);
    token = r.token;
    start();
  } catch (err) { $("join-error").textContent = err.message; }
});

function start() {
  $("join-panel").classList.add("hidden");
  connect();
}

// The connection

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { ws.send(JSON.stringify({ type: "auth", token })); reconnectDelay = 500; };
  ws.onmessage = (e) => handle(JSON.parse(e.data));
  ws.onclose = (e) => {
    $("status").textContent = "disconnected; reconnecting…";
    if (e.code === 4401) { $("status").textContent = "not a player in this game"; showJoin(); return; }
    setTimeout(connect, reconnectDelay);
    reconnectDelay = Math.min(reconnectDelay * 2, 8000);
  };
}

function send(msg) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
}

function handle(msg) {
  switch (msg.type) {
    case "welcome":
      me = msg.player;
      showBot(me);
      $("me").textContent = `you are ${me.name}${me.is_host ? " (host)" : ""}`;
      $("status").textContent = "";
      setGame(msg.game);
      break;
    case "game": setGame(msg.game); break;
    case "error":
      if (msg.fatal) { $("status").textContent = msg.message; }
      else if (msg.request && msg.request.type === "challenge_answer") {
        $("challenge-error").textContent = msg.message;
      } else { $("order-error").textContent = msg.message; }
      break;
    case "round_started":
      $("results").classList.add("hidden");
      break;
    case "news":
      if (msg.round === roundId && seat) {
        seat.news.push({ time: msg.time, headline: msg.headline, change: msg.change });
        scheduleRender();
      }
      break;
    case "lockstep_progress":
      showLockstep(msg);
      break;
    case "challenge":
      showChallenge(msg);
      break;
    case "leaderboard":
      boards = msg.boards;
      showBoards();
      break;
    case "challenge_saved":
      $("challenge-error").textContent = "";
      $("challenge-saved").textContent = `Saved: ${describeAnswer(msg.answer)}.`;
      break;
    case "live_snapshot":
      roundId = msg.round;
      seated = msg.seated;
      seat = fromSnapshot(msg.state);
      ladderCenter = null;
      $("trading").classList.remove("hidden");
      $("spectating").textContent = seated ? "" : "watching: you join the next round";
      render();
      break;
    case "live":
      if (msg.round === roundId && seat) { apply(seat, msg.m); scheduleRender(); }
      break;
    case "round_ended":
      showResults(msg.round, msg.results);
      roundId = null;
      seat = null;
      $("trading").classList.add("hidden");
      $("lockstep").classList.add("hidden");
      $("challenge").classList.add("hidden");
      $("briefing-panel").classList.add("hidden");
      $("ready-gate").classList.add("hidden");
      clearInterval(challengeTimer);
      break;
  }
}

// The lobby

function setGame(g) {
  game = g;
  $("game-name").textContent = g.name;
  $("lobby").classList.remove("hidden");
  $("players").innerHTML = g.players.map((p) =>
    `<li><span class="dot ${p.online ? "on" : ""}"></span>${esc(p.name)}` +
    `${p.is_host ? '<span class="badge">host</span>' : ""}` +
    `${p.kind === "bot" ? '<span class="badge">bot</span>' : ""}` +
    `${me && p.id === me.id ? '<span class="badge">you</span>' : ""}` +
    ` <span class="dim">${usd(p.balance)}</span>${p.bust ? '<span class="badge neg">bust</span>' : ""}</li>`).join("");
  const isHost = me && me.is_host;
  $("host-controls").classList.toggle("hidden", !isHost);
  if (isHost && $("market").options.length !== g.markets.length) {
    $("market").innerHTML = g.markets.map((m) =>
      `<option value="${esc(m.name)}">${esc(m.title)}</option>`).join("");
    describeMarket();
    showKindFields();
  }
  $("start-round").classList.toggle("hidden", !!g.current);
  $("stop-round").classList.toggle("hidden", !g.current);
  $("lobby").classList.toggle("in-round", !!g.current);
  showBriefing(g.current, g.ready_check);
  if (g.current) {
    $("round-title").textContent =
      `round ${g.current.number}: ${g.current.config.market_title || g.current.market}`;
  }
  const done = g.rounds.filter((r) => r.status !== "running");
  $("history").classList.toggle("hidden", done.length === 0);
  $("rounds").innerHTML = done.slice().reverse().map((r) =>
    `<tr><td>Round ${r.number}</td><td>${esc(r.kind)}</td><td>${esc(r.config.market_title || r.market || "")}</td>` +
    `<td class="dim">${esc(r.status)}</td><td><button data-round="${r.number}">Results</button></td></tr>`).join("");
  for (const b of $("rounds").querySelectorAll("button")) {
    b.onclick = async () => {
      const r = await get(`/api/games/${code}/rounds/${b.dataset.round}`);
      showResults(r.round, r.results);
    };
  }
}

// The briefing of the market round under way: before the market opens, in full, with who has
// read it; then in a compact panel below the trading screen while the round lasts.
function showBriefing(r, check) {
  const text = r && r.config && r.config.briefing;
  const gate = !!(text && check && check.round === r.id);
  $("ready-gate").classList.toggle("hidden", !gate);
  $("briefing-panel").classList.toggle("hidden", !text || gate);
  if (!text) return;
  const title = `round ${r.number}: ${r.config.market_title || r.market}`;
  $("briefing-title").textContent = title;
  $("ready-title").textContent = title;
  if ($("briefing").textContent !== text) $("briefing").textContent = text;
  if ($("ready-briefing").textContent !== text) $("ready-briefing").textContent = text;
  if (!gate) return;
  const ready = new Set(check.ready), waiting = new Set(check.waiting);
  const asked = game.players.filter((p) => check.asked.includes(p.id));
  $("ready-players").innerHTML = asked.map((p) =>
    `<li><span class="dot ${p.online ? "on" : ""}"></span>${esc(p.name)} ` +
    `<span class="${ready.has(p.id) ? "pos" : "dim"}">${ready.has(p.id) ? "ready"
      : waiting.has(p.id) ? "reading…" : "not connected"}</span></li>`).join("");
  const mine = me && check.asked.includes(me.id) && !ready.has(me.id);
  $("ready-button").classList.toggle("hidden", !mine);
  $("begin-now").classList.toggle("hidden", !(me && me.is_host));
  $("ready-status").textContent = check.waiting.length
    ? `waiting for ${check.waiting.length} to read it` : "opening the market…";
}
$("ready-button").onclick = () => send({ type: "ready" });
$("begin-now").onclick = async () => {
  try { await post(`/api/games/${code}/rounds/begin`, {}, token); }
  catch (err) { $("ready-status").textContent = err.message; }
};

function showLockstep(p) {
  if (!game || !game.current || game.current.kind !== "lockstep") return;
  $("lockstep").classList.remove("hidden");
  $("lockstep-title").textContent =
    `round ${game.current.number}: ${game.current.config.market_title || game.current.market}`;
  $("lockstep-clock").textContent = `${clock(p.time / SEC)} / ${clock(p.duration / SEC)}`;
  $("lockstep-bar").style.width = `${p.duration ? Math.min(100, (100 * p.time) / p.duration) : 0}%`;
  const done = new Set(p.done.map(String));
  const bots = game.players.filter((x) => x.kind === "bot");
  $("lockstep-body").innerHTML = bots.map((b) =>
    `<tr><td>${esc(b.name)}</td><td>${done.has(String(b.id)) ? "finished" : "trading"}</td>` +
    `<td><span class="dot ${b.online ? "on" : ""}"></span></td></tr>`).join("");
}

function showKindFields() {
  const kind = $("round-kind").value;
  const market = game && game.markets.find((m) => m.name === $("market").value);
  const show = { "live-only": kind === "live", "lockstep-only": kind === "lockstep",
    "challenge-only": kind === "challenge", "market-only": kind !== "challenge",
    "news-only": kind !== "challenge" && !!(market && market.news) };
  for (const [cls, on] of Object.entries(show)) {
    for (const el of document.querySelectorAll(`.${cls}`)) el.classList.toggle("hidden", !on);
  }
}

// Leaderboards

let boards = null;
let board = "overall";

function showBoards() {
  const any = boards && boards.overall.rows.length > 0;
  $("leaderboard").classList.toggle("hidden", !any);
  if (!any) return;
  $("board-tabs").innerHTML = Object.entries(boards).map(([key, b]) =>
    `<button data-board="${key}" class="${key === board ? "primary" : ""}">${esc(b.title)}</button>`).join("");
  for (const btn of $("board-tabs").querySelectorAll("button")) {
    btn.onclick = () => { board = btn.dataset.board; showBoards(); };
  }
  const b = boards[board], byMoney = b.by === "balance";
  $("board-value").textContent = byMoney ? "Balance" : "Points";
  $("board-body").innerHTML = b.rows.length ? b.rows.map((r) =>
    `<tr class="${me && r.player_id === me.id ? "pos" : ""}"><td>${r.rank}</td><td>${esc(r.name)}` +
    `${r.kind === "bot" ? '<span class="badge">bot</span>' : ""}</td>` +
    `<td class="num">${byMoney ? usd(r.balance) : r.points.toFixed(1)}</td><td class="num">${r.rounds}</td></tr>`).join("")
    : '<tr><td colspan="4" class="dim">No rounds of this kind yet.</td></tr>';
}

// Challenges

let challenge = null;
let challengeTimer = null;

function describeAnswer(a) {
  if (!a) return "nothing yet";
  if ("bid" in a) return `bid ${a.bid}`;
  if ("path" in a) return a.path.length ? `Shells → ${a.path.join(" → ")}` : "no trades";
  if ("fraction" in a) return `stake ${a.fraction}`;
  return JSON.stringify(a);
}

function showChallenge(c) {
  challenge = c;
  $("challenge").classList.remove("hidden");
  $("challenge-title").textContent = c.title;
  $("challenge-prompt").textContent = c.prompt;
  $("challenge-saved").textContent = `Your answer: ${describeAnswer(c.answer)}.`;
  const form = $("challenge-form");
  const data = $("challenge-data");
  data.innerHTML = "";
  if (c.kind === "auction") {
    form.innerHTML = `<label>Bid <input type="number" id="ans-bid" min="0" step="1" value="${c.answer ? c.answer.bid : ""}"></label>` +
      `<button class="primary">Save</button>`;
  } else if (c.kind === "kelly") {
    form.innerHTML = `<label>Fraction <input type="number" id="ans-fraction" min="0" max="1" step="0.001" value="${c.answer ? c.answer.fraction : ""}"></label>` +
      `<button class="primary">Save</button>`;
  } else if (c.kind === "conversion") {
    const g = c.data.goods;
    data.innerHTML = `<table><thead><tr><th>1 unit of ↓ buys →</th>${g.map((x) => `<th class="num">${esc(x)}</th>`).join("")}</tr></thead><tbody>` +
      c.data.rates.map((row, i) => `<tr><th>${esc(g[i])}</th>${row.map((r, j) => `<td class="num">${i === j ? "" : r}</td>`).join("")}</tr>`).join("") +
      "</tbody></table>";
    const opts = ['<option value="">—</option>', ...g.map((x) => `<option>${esc(x)}</option>`)].join("");
    const steps = [];
    for (let i = 0; i < c.data.max_trades; i++) steps.push(`<select class="ans-step">${opts}</select>`);
    form.innerHTML = `Shells → ${steps.join(" → ")} <button class="primary">Save</button>`;
    const path = c.answer ? c.answer.path : [];
    form.querySelectorAll(".ans-step").forEach((el, i) => { el.value = path[i] || ""; });
  }
  clearInterval(challengeTimer);
  const tick = () => {
    const left = Math.max(0, c.closes_at - Date.now() / 1000);
    $("challenge-clock").textContent = `Closes in ${clock(left)}.`;
  };
  tick();
  challengeTimer = setInterval(tick, 1000);
}

$("challenge-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!challenge) return;
  let answer;
  if (challenge.kind === "auction") answer = { bid: Number($("ans-bid").value) };
  else if (challenge.kind === "kelly") answer = { fraction: Number($("ans-fraction").value) };
  else answer = { path: [...document.querySelectorAll(".ans-step")].map((el) => el.value).filter((v) => v) };
  send({ type: "challenge_answer", answer });
});
$("round-kind").onchange = showKindFields;

function describeMarket() {
  const m = game.markets.find((x) => x.name === $("market").value);
  $("market-description").textContent = m ? m.description : "";
  previewBriefing();
}
$("market").onchange = () => { describeMarket(); showKindFields(); };

// The briefing players will get, for the market, length and news the host has chosen.
let briefingAsked = 0;
async function previewBriefing() {
  const name = $("market").value;
  if (!name) return;
  const ask = ++briefingAsked;
  const q = new URLSearchParams({ duration_s: $("duration").value, news: $("news-delay").value });
  try {
    const r = await get(`/api/kinds/${encodeURIComponent(name)}/briefing?${q}`);
    if (ask === briefingAsked) $("market-briefing").textContent = r.briefing;
  } catch {
    if (ask === briefingAsked) $("market-briefing").textContent = "";
  }
}
$("duration").onchange = previewBriefing;
$("news-delay").onchange = previewBriefing;

$("start-round").onclick = async () => {
  $("host-error").textContent = "";
  try {
    const kind = $("round-kind").value;
    const spec = { kind, duration_s: parseInt($("duration").value, 10) };
    if (kind === "live") spec.speed = parseFloat($("speed").value);
    if (kind === "lockstep") spec.turn_timeout_ms = parseInt($("turn-timeout").value, 10);
    if (kind === "challenge") spec.challenge = $("challenge-kind").value;
    else spec.market = $("market").value;
    const market = game.markets.find((m) => m.name === spec.market);
    if (kind !== "challenge" && market && market.news) spec.news = $("news-delay").value;
    await post(`/api/games/${code}/rounds`, spec, token);
  } catch (err) { $("host-error").textContent = err.message; }
};

$("stop-round").onclick = async () => {
  if (!confirm("End the round for everyone now?")) return;
  try { await post(`/api/games/${code}/rounds/stop`, {}, token); }
  catch (err) { $("host-error").textContent = err.message; }
};

// The seat, kept from the gateway's messages (crowdbook's docs/protocol.md)

function fromSnapshot(s) {
  s = s || {};
  return {
    time: s.time || 0, duration: s.duration || 0, started: !!s.started, cash: s.cash || 0,
    position: s.position || 0, fees: s.fees || 0, initialPosition: s.initial_position || 0,
    maxPosition: s.max_position || 0, maxOrder: s.max_order_quantity || 0,
    orders: new Map((s.orders || []).map((o) => [o.id, o])), bids: s.bids || [], asks: s.asks || [],
    last: s.last_trade, trades: (s.trades || []).slice(-60).reverse(), ended: s.ended,
    reference: s.reference_price || 0, initialCash: s.initial_cash || 0,
    balance: s.balance ?? 1000000,
    mids: new Map((s.mids || []).map(([q, p]) => [q, p])), myFills: (s.my_fills || []).slice(),
    news: (s.news || []).slice(),
  };
}

function apply(s, m) {
  if (typeof m.time === "number") s.time = Math.max(s.time, m.time);
  switch (m.type) {
    case "start": s.started = true; break;
    case "accepted":
      s.orders.set(m.id, { id: m.id, side: m.side, order_type: m.order_type, price: m.price, leaves: m.quantity });
      break;
    case "modified": {
      const o = s.orders.get(m.id);
      if (o) { o.price = m.price; o.leaves = m.quantity; }
      break;
    }
    case "filled": {
      const sign = m.side === "buy" ? 1 : -1;
      s.position += sign * m.quantity;
      s.cash -= sign * m.quantity * m.price;
      s.fees += m.fee / 1000;
      const o = s.orders.get(m.id);
      if (o) { if (m.leaves > 0) o.leaves = m.leaves; else s.orders.delete(m.id); }
      s.myFills.push([m.time, m.side, m.price, m.quantity]);
      break;
    }
    case "cancelled": s.orders.delete(m.id); break;
    case "rejected":
      $("order-error").textContent = `${m.request} rejected: ${m.reason}`;
      break;
    case "trade":
      s.last = m.price;
      s.trades.unshift({ time: m.time, price: m.price, quantity: m.quantity, aggressor: m.aggressor });
      if (s.trades.length > 60) s.trades.pop();
      break;
    case "top":
      s.bids = mergeTop(s.bids, m.bid);
      s.asks = mergeTop(s.asks, m.ask);
      noteMid(s);
      break;
    case "depth":
      s.bids = m.bids.map((l) => [l.price, l.quantity]);
      s.asks = m.asks.map((l) => [l.price, l.quantity]);
      noteMid(s);
      break;
    case "end":
      s.ended = m;
      s.cash = m.cash - (s.initialCash || 0);
      s.position = m.position;
      s.fees = m.fees / 1000;
      s.orders.clear();
      break;
  }
}

function mergeTop(levels, top) {
  if (!top) return [];
  if (levels.length && levels[0][0] === top.price) return [[top.price, top.quantity], ...levels.slice(1)];
  return [[top.price, top.quantity]];
}

// Drawing

let renderPending = false;
function scheduleRender() {
  if (renderPending) return;
  renderPending = true;
  requestAnimationFrame(() => { renderPending = false; render(); });
}

function mark(s) {
  if (s.bids.length && s.asks.length) return (s.bids[0][0] + s.asks[0][0]) / 2;
  return s.last || s.reference;
}

// Money and prices: a tick is a cent, so amounts in tick-lots are cents.
function usd(cents, signed = false) {
  const v = Number(cents) / 100;
  const s = `$${Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return v < 0 ? `-${s}` : signed && v > 0 ? `+${s}` : s;
}
function px(ticks) { return (Number(ticks) / 100).toFixed(2); }

function fmt(x, digits = 1) {
  const v = Number(x);
  return (v > 0 ? "+" : "") + v.toFixed(digits);
}

function render() {
  const s = seat;
  if (!s) return;
  const pnl = s.cash + (s.position - s.initialPosition) * mark(s) - s.fees;
  const equity = s.balance + pnl;
  const power = mark(s) > 0 ? Math.max(0, Math.floor(equity / mark(s) + 1e-9)) : 0;
  $("position").textContent = s.position;
  $("pnl").textContent = usd(pnl, true);
  $("pnl").className = `v ${pnl > 0 ? "pos" : pnl < 0 ? "neg" : ""}`;
  $("cash").textContent = usd(equity);
  $("fees").textContent = usd(s.fees);
  $("limits").textContent = seated
    ? `Started the round with ${usd(s.balance)}. Buying power: ${Math.min(power, s.maxPosition)} lots either way ` +
      `(most ${s.maxPosition}), ${s.maxOrder} an order. P&L values your position at the middle of the market.`
    : "";
  const t = s.time / SEC, d = s.duration / SEC;
  $("clock-text").textContent = s.ended ? "closed" : s.started
    ? `${clock(t)} / ${clock(d)}  (${clock(Math.max(0, d - t))} left)` : "starting…";
  $("clock-bar").style.width = `${d ? Math.min(100, (100 * t) / d) : 0}%`;
  renderLadder(s);
  renderOrders(s);
  renderChart(s);
  $("news-panel").classList.toggle("hidden", !(game && game.current && game.current.config.news));
  $("news").innerHTML = s.news.slice().reverse().map((n) =>
    `<tr><td class="dim">${clock(n.time / SEC)}</td><td class="${n.change > 0 ? "pos" : n.change < 0 ? "neg" : ""}">${esc(n.headline)}</td></tr>`).join("")
    || '<tr><td class="dim">No news yet.</td></tr>';
  $("tape").innerHTML = s.trades.slice(0, 40).map((tr) =>
    `<tr><td class="dim">${clock(tr.time / SEC)}</td><td class="num ${tr.aggressor === "buy" ? "pos" : "neg"}">${px(tr.price)}</td>` +
    `<td class="num">${tr.quantity}</td></tr>`).join("");
  if (!$("price").value && s.bids.length) $("price").value = px(s.bids[0][0]);
}

// The price chart: the middle of the market every quarter second of simulated time, drawn as a
// smooth curve that never overshoots (monotone cubic), over the round so far or a recent window,
// with the seat's own fills and the headlines marked. A crosshair follows the pointer.
const MID_STEP = SEC / 4;
const RANGES = { "30s": 30, "1m": 60, "5m": 300, All: null };
let chartRange = "All";
let chartHover = null;  // the pointer's x on the chart, in CSS pixels

function noteMid(s) {
  if (s.bids.length && s.asks.length) s.mids.set(Math.floor(s.time / MID_STEP), (s.bids[0][0] + s.asks[0][0]) / 2);
}

$("chart-ranges").innerHTML = Object.keys(RANGES).map((r) =>
  `<button data-range="${r}" class="${r === chartRange ? "primary" : ""}">${r}</button>`).join("");
$("chart-ranges").addEventListener("click", (e) => {
  const r = e.target.dataset && e.target.dataset.range;
  if (!r) return;
  chartRange = r;
  for (const b of $("chart-ranges").querySelectorAll("button")) b.classList.toggle("primary", b.dataset.range === r);
  scheduleRender();
});
$("chart").addEventListener("pointermove", (e) => {
  chartHover = e.clientX - $("chart").getBoundingClientRect().left;
  scheduleRender();
});
$("chart").addEventListener("pointerleave", () => { chartHover = null; scheduleRender(); });

// Fritsch–Carlson tangents: a cubic through each pair of points that stays between them.
function monotoneTangents(xs, ys) {
  const n = xs.length, d = [], m = new Array(n).fill(0);
  for (let i = 0; i < n - 1; i++) d.push((ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i] || 1));
  if (n > 1) { m[0] = d[0]; m[n - 1] = d[n - 2]; }
  for (let i = 1; i < n - 1; i++) m[i] = d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2;
  for (let i = 0; i < n - 1; i++) {
    if (d[i] === 0) { m[i] = 0; m[i + 1] = 0; continue; }
    const a = m[i] / d[i], b = m[i + 1] / d[i], h = a * a + b * b;
    if (h > 9) { const t = 3 / Math.sqrt(h); m[i] = t * a * d[i]; m[i + 1] = t * b * d[i]; }
  }
  return m;
}

function tracePath(ctx, xs, ys) {
  const m = monotoneTangents(xs, ys);
  ctx.moveTo(xs[0], ys[0]);
  for (let i = 0; i < xs.length - 1; i++) {
    const dx = (xs[i + 1] - xs[i]) / 3;
    ctx.bezierCurveTo(xs[i] + dx, ys[i] + m[i] * dx, xs[i + 1] - dx, ys[i + 1] - m[i + 1] * dx, xs[i + 1], ys[i + 1]);
  }
}

function renderChart(s) {
  const canvas = $("chart");
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (!w || !h) return;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const css = getComputedStyle(document.documentElement);
  const color = (name) => css.getPropertyValue(name).trim();
  const tip = $("chart-tip");
  // The window: from the start, or the chosen span before now, to now.
  const now = Math.max(s.time / SEC, 1);
  const span = RANGES[chartRange];
  const t0 = span ? Math.max(0, now - span) : 0;
  const pts = [...s.mids.entries()].sort((a, b) => a[0] - b[0])
    .map(([q, p]) => [(q * MID_STEP) / SEC, p]);
  // The line starts at the last point before the window and runs on to now at the latest mid.
  let first = 0;
  while (first < pts.length - 1 && pts[first + 1][0] <= t0) first++;
  const shown = pts.slice(first).map(([t, p]) => [Math.max(t, t0), p]);
  if (shown.length) shown.push([now, shown[shown.length - 1][1]]);
  const fills = s.myFills.filter((f) => f[0] / SEC >= t0);
  const news = s.news.filter((n) => n.time / SEC >= t0);
  if (!shown.length) {
    ctx.fillStyle = color("--dim");
    ctx.font = "12px system-ui";
    ctx.fillText("Waiting for the market…", 8, h / 2);
    tip.classList.add("hidden");
    return;
  }
  const prices = shown.map((p) => p[1]).concat(fills.map((f) => f[2]));
  let lo = Math.min(...prices), hi = Math.max(...prices);
  const pad = Math.max(1, (hi - lo) * 0.15);
  lo -= pad; hi += pad;
  const left = 8, right = 56, top = 10, bottom = 20;
  const x = (t) => left + ((w - left - right) * (t - t0)) / Math.max(now - t0, 1e-9);
  const y = (p) => top + ((h - top - bottom) * (hi - p)) / (hi - lo);
  // Grid and labels: whole ticks on the right, times along the bottom.
  ctx.font = "11px ui-monospace, monospace";
  ctx.lineWidth = 1;
  const step = Math.max(1, Math.ceil((hi - lo) / 4));
  for (let p = Math.ceil(lo / step) * step; p <= hi; p += step) {
    ctx.strokeStyle = color("--line");
    ctx.beginPath(); ctx.moveTo(left, Math.round(y(p)) + 0.5); ctx.lineTo(w - right, Math.round(y(p)) + 0.5); ctx.stroke();
    ctx.fillStyle = color("--dim");
    ctx.fillText(px(p), w - right + 6, y(p) + 4);
  }
  ctx.fillStyle = color("--dim");
  ctx.fillText(clock(t0), left, h - 5);
  const nowLabel = clock(now);
  ctx.fillText(nowLabel, w - right - ctx.measureText(nowLabel).width, h - 5);
  const xs = shown.map((p) => x(p[0])), ys = shown.map((p) => y(p[1]));
  const accent = color("--accent");
  // A soft fill under the line, fading downwards.
  const grad = ctx.createLinearGradient(0, top, 0, h - bottom);
  grad.addColorStop(0, accent + "40");
  grad.addColorStop(1, accent + "00");
  ctx.beginPath();
  tracePath(ctx, xs, ys);
  ctx.lineTo(xs[xs.length - 1], h - bottom);
  ctx.lineTo(xs[0], h - bottom);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.beginPath();
  tracePath(ctx, xs, ys);
  ctx.strokeStyle = accent;
  ctx.lineWidth = 2;
  ctx.lineJoin = "round";
  ctx.stroke();
  // Headlines, as dashed lines at the moment they came out.
  ctx.setLineDash([3, 3]);
  ctx.lineWidth = 1;
  for (const n of news) {
    ctx.strokeStyle = color(n.change > 0 ? "--bid" : n.change < 0 ? "--ask" : "--dim");
    ctx.beginPath(); ctx.moveTo(Math.round(x(n.time / SEC)) + 0.5, top); ctx.lineTo(Math.round(x(n.time / SEC)) + 0.5, h - bottom); ctx.stroke();
  }
  ctx.setLineDash([]);
  // The seat's own fills: a triangle up for each buy, down for each sell. The bot this tab runs
  // has its fills drawn hollow.
  for (const [t, side, p] of botFills(roundId).filter((f) => f[0] / SEC >= t0)) {
    const cx = x(t / SEC), cy = y(p), r = 5;
    ctx.strokeStyle = color(side === "buy" ? "--bid" : "--ask");
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    if (side === "buy") { ctx.moveTo(cx, cy - r); ctx.lineTo(cx - r, cy + r); ctx.lineTo(cx + r, cy + r); }
    else { ctx.moveTo(cx, cy + r); ctx.lineTo(cx - r, cy - r); ctx.lineTo(cx + r, cy - r); }
    ctx.closePath();
    ctx.stroke();
  }
  ctx.lineWidth = 1;
  // The seat's own fills: a triangle up for each buy, down for each sell.
  for (const [t, side, p] of fills) {
    const cx = x(t / SEC), cy = y(p), r = 5;
    ctx.fillStyle = color(side === "buy" ? "--bid" : "--ask");
    ctx.beginPath();
    if (side === "buy") { ctx.moveTo(cx, cy - r); ctx.lineTo(cx - r, cy + r); ctx.lineTo(cx + r, cy + r); }
    else { ctx.moveTo(cx, cy + r); ctx.lineTo(cx - r, cy - r); ctx.lineTo(cx + r, cy - r); }
    ctx.fill();
  }
  // The price now: a dot at the line's end, and its value on the axis.
  const lastP = shown[shown.length - 1][1], ex = xs[xs.length - 1], ey = ys[ys.length - 1];
  ctx.fillStyle = accent;
  ctx.beginPath(); ctx.arc(ex, ey, 4, 0, 2 * Math.PI); ctx.fill();
  const label = px(lastP);
  ctx.fillRect(w - right + 2, ey - 9, right - 4, 18);
  ctx.fillStyle = "#fff";
  ctx.fillText(label, w - right + 6, ey + 4);
  // The crosshair, at the point nearest the pointer.
  if (chartHover !== null && chartHover >= left && chartHover <= w - right) {
    let i = 0;
    for (let j = 1; j < xs.length; j++) if (Math.abs(xs[j] - chartHover) < Math.abs(xs[i] - chartHover)) i = j;
    ctx.strokeStyle = color("--dim");
    ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(Math.round(xs[i]) + 0.5, top); ctx.lineTo(Math.round(xs[i]) + 0.5, h - bottom); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(left, Math.round(ys[i]) + 0.5); ctx.lineTo(w - right, Math.round(ys[i]) + 0.5); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = accent;
    ctx.beginPath(); ctx.arc(xs[i], ys[i], 4, 0, 2 * Math.PI); ctx.fill();
    const p = shown[i][1];
    tip.textContent = `${clock(shown[i][0])}  $${px(p)}`;
    tip.classList.remove("hidden");
    tip.style.left = `${Math.min(Math.max(xs[i] - tip.offsetWidth / 2, 0), w - tip.offsetWidth)}px`;
  } else {
    tip.classList.add("hidden");
  }
}

function clock(sec) {
  const m = Math.floor(sec / 60), r = Math.floor(sec % 60);
  return `${m}:${String(r).padStart(2, "0")}`;
}

function renderLadder(s) {
  const bids = new Map(s.bids.map(([p, q]) => [p, q]));
  const asks = new Map(s.asks.map(([p, q]) => [p, q]));
  const mine = new Map();
  for (const o of s.orders.values()) {
    if (o.price == null) continue;
    const k = `${o.side}:${o.price}`;
    mine.set(k, (mine.get(k) || 0) + o.leaves);
  }
  const mid = Math.round(mark(s));
  const half = Math.floor(LADDER_ROWS / 2);
  // Keep the ladder still until the market moves well away from its middle.
  if (ladderCenter === null || Math.abs(mid - ladderCenter) > half / 2) ladderCenter = mid;
  const bestBid = s.bids.length ? s.bids[0][0] : null, bestAsk = s.asks.length ? s.asks[0][0] : null;
  const rows = [];
  for (let p = ladderCenter + half; p >= ladderCenter - half; p--) {
    const cls = [p === s.last ? "last" : "", p === bestBid ? "best-bid" : "", p === bestAsk ? "best-ask" : ""].join(" ");
    const mb = mine.get(`buy:${p}`), ma = mine.get(`sell:${p}`);
    rows.push(`<tr class="${cls}" data-price="${p}">` +
      `<td class="mine" data-side="buy">${mb || ""}</td>` +
      `<td class="bidq" data-side="buy">${bids.get(p) || ""}</td>` +
      `<td class="price">${px(p)}</td>` +
      `<td class="askq" data-side="sell">${asks.get(p) || ""}</td>` +
      `<td class="mine" data-side="sell">${ma || ""}</td></tr>`);
  }
  $("ladder").innerHTML = rows.join("");
}

// The ladder and the order list are redrawn with every update of the book, several times a
// second, so a click (a press and a release, a fraction of a second apart) would often start on one
// drawing and end on the next, and never fire. They act on the press instead, as trading ladders do.
$("ladder").addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  const td = e.target.closest("td");
  const tr = e.target.closest("tr");
  if (!td || !tr || !seat || !seated) return;
  const price = parseInt(tr.dataset.price, 10);
  const side = td.dataset.side;
  if (td.classList.contains("mine")) {
    for (const o of seat.orders.values()) {
      if (o.side === side && o.price === price) send({ type: "cancel", id: o.id });
    }
  } else if (td.classList.contains("bidq") || td.classList.contains("askq")) {
    limit(side, price);
  }
});

function renderOrders(s) {
  const list = [...s.orders.values()].sort((a, b) => (b.price || 0) - (a.price || 0));
  $("orders").innerHTML = list.map((o) =>
    `<tr><td class="${o.side === "buy" ? "pos" : "neg"}">${o.side}</td><td class="num">${o.price != null ? px(o.price) : "market"}</td>` +
    `<td class="num">${o.leaves}</td><td><button data-id="${o.id}">Cancel</button></td></tr>`).join("");
}

$("orders").addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  const id = e.target.dataset && e.target.dataset.id;
  if (id) send({ type: "cancel", id: parseInt(id, 10) });
});

function qty() { return parseInt($("qty").value, 10); }

function limit(side, price) {
  $("order-error").textContent = "";
  if (!(qty() > 0) || !(price > 0)) { $("order-error").textContent = "a positive quantity and price"; return; }
  send({ type: "new", side, order_type: "limit", price, quantity: qty() });
}

function market(side) {
  $("order-error").textContent = "";
  if (!(qty() > 0)) { $("order-error").textContent = "a positive quantity"; return; }
  send({ type: "new", side, order_type: "market", quantity: qty() });
}

// The ticket's price is in dollars; orders are in ticks, cents.
const ticketPrice = () => Math.round(parseFloat($("price").value) * 100);
$("buy-limit").onclick = () => limit("buy", ticketPrice());
$("sell-limit").onclick = () => limit("sell", ticketPrice());
$("buy-market").onclick = () => market("buy");
$("sell-market").onclick = () => market("sell");
$("cancel-all").onclick = () => send({ type: "cancel_all" });

// Results

function showResults(round, results) {
  $("results").classList.remove("hidden");
  $("results-title").textContent = `round ${round.number}: ${round.config.market_title || round.config.title || round.market || round.kind}`;
  const sol = round.config.solution;
  $("solution").classList.toggle("hidden", !sol);
  if (sol) {
    const mine = me && results.find((r) => r.player_id === me.id && r.details.explained);
    $("solution").innerHTML = (sol.setup ? `${esc(sol.setup)}<br>` : "") +
      (mine ? `Your answer, ${esc(describeAnswer(mine.details.answer))}, ${esc(mine.details.explained)}.<br>` : "") +
      (sol.explained
        ? `The best answer, ${esc(describeAnswer(sol.answer))}, ${esc(sol.explained)}.`
        : `The best answer: ${esc(describeAnswer(sol.answer))}, worth ${Number(sol.value).toFixed(2)}.`) +
      ` Scores are out of 100: the best answer scores 100 and doing nothing 0.`;
  }
  const money = round.kind !== "challenge";
  $("results-score").textContent = money ? "P&L" : "Score";
  $("results-return").textContent = money ? "Return" : "";
  $("results-body").innerHTML = results.map((r) =>
    `<tr class="${me && r.player_id === me.id ? "pos" : ""}"><td>${r.rank}</td><td>${esc(r.name)}${r.kind === "bot" ? '<span class="badge">bot</span>' : ""}</td>` +
    `<td class="num">${money ? usd(r.score, true) : fmt(r.score)}</td>` +
    `<td class="num">${money && r.details.return_pct != null ? `${fmt(r.details.return_pct, 2)}%` : ""}</td><td class="num">${r.details.position ?? (r.details.answered === false ? "no answer" : r.details.answer ? esc(describeAnswer(r.details.answer)) : "")}</td>` +
    `<td class="num">${r.details.fees != null ? usd(r.details.fees) : ""}</td>` +
    `<td class="num">${r.details.traded ?? ""}</td></tr>`).join("");
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

if (new URLSearchParams(location.search).has("new")) token = null;
if (token) start(); else showJoin();
