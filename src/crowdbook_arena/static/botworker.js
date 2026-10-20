// A bot pasted into the game page, running in the player's own tab: Pyodide in this Web Worker,
// with the kit's pure-Python modules, plays the bot from its own seat over its own WebSocket.
// Nothing of the bot's code goes to the server. The page gets a report for everything the bot
// does ({type: "turn" | "started" | "ended" | "print" | "error" | "warning" | ...}).
//
// The page starts it with {type: "start", kit, source}; once the bot has loaded, the worker
// reports "ready" and the page sends {type: "connect", token, wsUrl}. Ending the worker ends the
// bot, and the server sees its connection close, as when a tab is closed.

let ws = null;
let runner = null;

function report(msg) { postMessage(msg); }

onmessage = (e) => {
  const m = e.data || {};
  if (m.type === "start") {
    start(m).catch((err) => {
      report({ type: "error", message: String((err && err.message) || err) });
      report({ type: "stopped", message: "the bot could not start" });
    });
  } else if (m.type === "connect" && runner && !ws) {
    connect(m);
  }
};

async function start({ kit, source }) {
  report({ type: "phase", message: "loading Python…" });
  importScripts(`${kit.pyodide}pyodide.js`);
  const py = await loadPyodide({
    indexURL: kit.pyodide,
    stdout: (text) => report({ type: "print", text }),
    stderr: (text) => report({ type: "print", text }),
  });
  const dir = "/home/pyodide/arena_kit";
  py.FS.mkdirTree(dir);
  py.FS.writeFile(`${dir}/__init__.py`, kit.init);
  for (const [name, text] of Object.entries(kit.files)) py.FS.writeFile(`${dir}/${name}`, text);
  py.runPython("import sys\nif '/home/pyodide' not in sys.path: sys.path.insert(0, '/home/pyodide')");
  const browser = py.pyimport("arena_kit.browser");

  runner = browser.start(source, (text) => { if (ws) ws.send(text); },
    (text) => report(JSON.parse(text)));
  if (!runner) {
    report({ type: "stopped", message: "fix the code and deploy again" });
    return;
  }
  report({ type: "ready" });
}

function connect({ token, wsUrl }) {
  report({ type: "phase", message: "connecting…" });
  // Messages are handled one at a time, in order, as `arena-kit join` handles them.
  let queue = Promise.resolve();
  ws = new WebSocket(wsUrl);
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "auth", token }));
    report({ type: "phase", message: "playing: waiting for rounds" });
  };
  ws.onmessage = (ev) => {
    queue = queue.then(() => runner.handle(ev.data)).catch((err) =>
      report({ type: "error", message: String((err && err.message) || err) }));
  };
  ws.onclose = (ev) => {
    report({ type: "stopped", message: `disconnected${ev.reason ? `: ${ev.reason}` : ""}` });
  };
}
