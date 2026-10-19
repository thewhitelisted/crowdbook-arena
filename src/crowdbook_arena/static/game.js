// The game page: joining, the lobby, the trading screen of a live round, and results.
import { get, knownPlayers, loadToken, post, saveToken, useToken } from "/static/api.js";

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
      $("me").textContent = `you are ${me.name}${me.is_host ? " (host)" : ""}`;
      $("status").textContent = "";
      setGame(msg.game);
      break;
    case "game": setGame(msg.game); break;
    case "error":
      if (msg.fatal) { $("status").textContent = msg.message; }
      else { $("order-error").textContent = msg.message; }
      break;
    case "round_started":
      $("results").classList.add("hidden");
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
    `${me && p.id === me.id ? '<span class="badge">you</span>' : ""}</li>`).join("");
  const isHost = me && me.is_host;
  $("host-controls").classList.toggle("hidden", !isHost);
  if (isHost && $("market").options.length !== g.markets.length) {
    $("market").innerHTML = g.markets.map((m) =>
      `<option value="${esc(m.name)}">${esc(m.title)}</option>`).join("");
    describeMarket();
  }
  $("start-round").classList.toggle("hidden", !!g.current);
  $("stop-round").classList.toggle("hidden", !g.current);
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

function describeMarket() {
  const m = game.markets.find((x) => x.name === $("market").value);
  $("market-description").textContent = m ? m.description : "";
}
$("market").onchange = describeMarket;

$("start-round").onclick = async () => {
  $("host-error").textContent = "";
  try {
    await post(`/api/games/${code}/rounds`, {
      kind: "live", market: $("market").value,
      duration_s: parseInt($("duration").value, 10), speed: parseFloat($("speed").value),
    }, token);
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
      break;
    case "depth":
      s.bids = m.bids.map((l) => [l.price, l.quantity]);
      s.asks = m.asks.map((l) => [l.price, l.quantity]);
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

function fmt(x, digits = 1) {
  const v = Number(x);
  return (v > 0 ? "+" : "") + v.toFixed(digits);
}

function render() {
  const s = seat;
  if (!s) return;
  const pnl = s.cash + (s.position - s.initialPosition) * mark(s) - s.fees;
  $("position").textContent = s.position;
  $("pnl").textContent = fmt(pnl);
  $("pnl").className = `v ${pnl > 0 ? "pos" : pnl < 0 ? "neg" : ""}`;
  $("cash").textContent = Math.round(s.cash);
  $("fees").textContent = s.fees.toFixed(1);
  $("limits").textContent = seated
    ? `Limits: ${s.maxPosition} lots either way, ${s.maxOrder} an order. P&L values your position at the middle of the market.`
    : "";
  const t = s.time / SEC, d = s.duration / SEC;
  $("clock-text").textContent = s.ended ? "closed" : s.started
    ? `${clock(t)} / ${clock(d)}  (${clock(Math.max(0, d - t))} left)` : "starting…";
  $("clock-bar").style.width = `${d ? Math.min(100, (100 * t) / d) : 0}%`;
  renderLadder(s);
  renderOrders(s);
  $("tape").innerHTML = s.trades.slice(0, 40).map((tr) =>
    `<tr><td class="dim">${clock(tr.time / SEC)}</td><td class="num ${tr.aggressor === "buy" ? "pos" : "neg"}">${tr.price}</td>` +
    `<td class="num">${tr.quantity}</td></tr>`).join("");
  if (!$("price").value && s.bids.length) $("price").value = s.bids[0][0];
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
      `<td class="price">${p}</td>` +
      `<td class="askq" data-side="sell">${asks.get(p) || ""}</td>` +
      `<td class="mine" data-side="sell">${ma || ""}</td></tr>`);
  }
  $("ladder").innerHTML = rows.join("");
}

$("ladder").addEventListener("click", (e) => {
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
    `<tr><td class="${o.side === "buy" ? "pos" : "neg"}">${o.side}</td><td class="num">${o.price ?? "market"}</td>` +
    `<td class="num">${o.leaves}</td><td><button data-id="${o.id}">Cancel</button></td></tr>`).join("");
}

$("orders").addEventListener("click", (e) => {
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

$("buy-limit").onclick = () => limit("buy", parseInt($("price").value, 10));
$("sell-limit").onclick = () => limit("sell", parseInt($("price").value, 10));
$("buy-market").onclick = () => market("buy");
$("sell-market").onclick = () => market("sell");
$("cancel-all").onclick = () => send({ type: "cancel_all" });

// Results

function showResults(round, results) {
  $("results").classList.remove("hidden");
  $("results-title").textContent = `round ${round.number}: ${round.config.market_title || round.market || round.kind}`;
  $("results-body").innerHTML = results.map((r) =>
    `<tr class="${me && r.player_id === me.id ? "pos" : ""}"><td>${r.rank}</td><td>${esc(r.name)}${r.kind === "bot" ? '<span class="badge">bot</span>' : ""}</td>` +
    `<td class="num">${fmt(r.score)}</td><td class="num">${r.details.position ?? ""}</td>` +
    `<td class="num">${r.details.fees != null ? Number(r.details.fees).toFixed(1) : ""}</td>` +
    `<td class="num">${r.details.traded ?? ""}</td></tr>`).join("");
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

if (new URLSearchParams(location.search).has("new")) token = null;
if (token) start(); else showJoin();
