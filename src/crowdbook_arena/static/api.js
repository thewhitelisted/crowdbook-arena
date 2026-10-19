// Calls to the arena's API, and where a browser keeps its player tokens.
//
// Each tab is one player: its token is in the tab's session storage, so a second tab can join the
// same game as someone else. Every player this browser has been in a game is also listed in local
// storage, so that a tab that was closed can carry on as that player.

function safe(fn, fallback) { try { return fn(); } catch { return fallback; } }

export function saveToken(code, token, name) {
  safe(() => sessionStorage.setItem(`arena:${code}`, token));
  const ids = knownPlayers(code).filter((p) => p.token !== token);
  ids.push({ name, token });
  safe(() => localStorage.setItem(`arena:ids:${code}`, JSON.stringify(ids)));
}

export function loadToken(code) { return safe(() => sessionStorage.getItem(`arena:${code}`), null); }

export function knownPlayers(code) {
  return safe(() => JSON.parse(localStorage.getItem(`arena:ids:${code}`) || "[]"), []);
}

export function useToken(code, token) { safe(() => sessionStorage.setItem(`arena:${code}`, token)); }

export async function post(path, body, token) {
  const headers = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const r = await fetch(path, { method: "POST", headers, body: JSON.stringify(body || {}) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}

export async function get(path) {
  const r = await fetch(path);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
  return data;
}
