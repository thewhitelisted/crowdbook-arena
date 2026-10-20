// The bot panel: a bot file pasted into the page, run in this tab (botworker.js) as a bot player
// in the game, and what it does, shown as it does it. The bot's token, name and code are kept in
// the tab's session storage, so a reload carries on where the tab left off.
import { get, post } from "/static/api.js";

const $ = (id) => document.getElementById(id);
const OUTPUT_LINES = 200;
const FILLS_SHOWN = 12;

let code = null;
let worker = null;
let kit = null;
let round = null;     // the round the bot is trading, and its fills there
let fills = [];
let output = [];

function stored() {
  try { return JSON.parse(sessionStorage.getItem(`arena:bot:${code}`) || "null") || {}; } catch { return {}; }
}
function store(data) {
  try { sessionStorage.setItem(`arena:bot:${code}`, JSON.stringify({ ...stored(), ...data })); } catch { /* private mode */ }
}

function usd(cents) {
  const v = Math.round(Number(cents)) / 100;
  return `${v < 0 ? "-" : v > 0 ? "+" : ""}$${Math.abs(v).toFixed(2)}`;
}
function px(ticks) { return (Number(ticks) / 100).toFixed(2); }
function level(l) { return l ? `${l[1]} @ ${px(l[0])}` : "none"; }

// The bot's fills in a round, as [time, side, price, quantity], for the price chart.
export function botFills(roundId) { return round === roundId ? fills : []; }

export function setupBot(gameCode) {
  code = gameCode;
  const s = stored();
  if (s.source) { $("bot-code").value = s.source; $("bot-details").open = true; }
  if (s.name) $("bot-name").value = s.name;
  $("bot-deploy").onclick = () => deploy().catch((err) => fail(err.message));
  $("bot-stop").onclick = () => { stop("stopped"); store({ running: false }); };
}

// Called once the page knows who the player is: names the bot, and restarts it after a reload.
export function showBot(me) {
  $("bot-panel").classList.toggle("hidden", me.kind !== "human");
  if (!$("bot-name").value) $("bot-name").value = `${me.name.slice(0, 18)}'s bot`;
  if (stored().running && !worker) deploy().catch((err) => fail(err.message));
}

function fail(message) {
  $("bot-error").textContent = message;
}

function print(text, cls = "") {
  output.push({ text, cls });
  output = output.slice(-OUTPUT_LINES);
  const el = $("bot-output");
  el.innerHTML = "";
  for (const line of output) {
    const div = document.createElement("div");
    div.textContent = line.text;
    if (line.cls) div.className = line.cls;
    el.appendChild(div);
  }
  el.scrollTop = el.scrollHeight;
}

async function deploy() {
  fail("");
  const source = $("bot-code").value;
  const name = $("bot-name").value.trim();
  if (!source.trim()) throw new Error("paste a bot file first");
  stop("");
  store({ name, source, running: true });
  kit = kit || await get("/api/kit");
  output = [];
  print(`starting ${name}`, "dim");
  $("bot-deploy").textContent = "Deploy again";
  $("bot-stop").classList.remove("hidden");
  const w = new Worker("/static/botworker.js");
  worker = w;
  w.onmessage = (e) => {
    if (e.data.type === "ready") join(w, name).catch((err) => { fail(err.message); stop("stopped"); });
    else onReport(e.data);
  };
  w.onerror = (e) => { fail(e.message || "the bot's worker failed"); stop("stopped"); };
  w.postMessage({ type: "start", kit, source });
}

// Once the bot's code has loaded: its seat in the game (the one this tab had, under the same
// name), and its connection.
async function join(w, name) {
  let { token, tokenName } = stored();
  if (!token || tokenName !== name) {
    token = (await post(`/api/games/${code}/join`, { name, kind: "bot" })).token;
    store({ token, tokenName: name });
  }
  if (worker !== w) return;  // stopped or deployed again meanwhile
  const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  w.postMessage({ type: "connect", token, wsUrl });
}

function stop(phase) {
  if (worker) { worker.terminate(); worker = null; }
  $("bot-stop").classList.add("hidden");
  if (phase !== "") $("bot-phase").textContent = phase;
}

function onReport(r) {
  switch (r.type) {
    case "phase": $("bot-phase").textContent = r.message; break;
    case "print": print(r.text); break;
    case "warning": print(r.message, "dim"); break;
    case "error":
      fail(r.message);
      print(r.trace || r.message, "neg");
      break;
    case "refused":
      fail(r.message);
      stop("stopped");
      store({ running: false });
      break;
    case "stopped":
      print(r.message, "dim");
      stop(r.message);
      store({ running: false });
      break;
    case "started":
      round = r.round;
      fills = [];
      $("bot-phase").textContent = `trading round ${r.round ?? ""} (${r.mode})`;
      break;
    case "turn": showTurn(r); break;
    case "ended":
      $("bot-phase").textContent = r.rank
        ? `round ended: rank ${r.rank} of ${r.of}, score ${Number(r.score).toFixed(1)}`
        : "round ended";
      break;
  }
}

function showTurn(r) {
  round = r.round;
  fills = r.fills;
  $("bot-position").textContent = r.position > 0 ? `+${r.position}` : `${r.position}`;
  $("bot-pnl").textContent = usd(r.pnl);
  $("bot-pnl").className = `v ${r.pnl > 0 ? "pos" : r.pnl < 0 ? "neg" : ""}`;
  $("bot-fees").textContent = usd(-r.fees);
  $("bot-quotes").textContent = `bid ${level(r.resting.bid)}, offer ${level(r.resting.ask)}`;
  $("bot-fills").innerHTML = fills.slice(-FILLS_SHOWN).reverse().map(([, side, price, qty]) =>
    `<tr><td class="${side === "buy" ? "pos" : "neg"}">${side === "buy" ? "buy" : "sell"}</td><td class="num">${Number(qty)}</td>` +
    `<td class="num">${px(price)}</td></tr>`).join("")
    || '<tr><td class="dim">No fills yet.</td></tr>';
}
