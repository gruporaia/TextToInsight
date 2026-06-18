// Wrappers de fetch para a API do backend. `credentials: "include"` garante que o
// cookie de sessao (t2i_session) viaje em todas as chamadas, mantendo a mesma
// ChatSession no servidor entre requisicoes.

async function postJSON(url, body) {
  const resp = await fetch(url, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || `Erro ${resp.status}`);
  }
  return data;
}

async function getJSON(url) {
  const resp = await fetch(url, { credentials: "include" });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(data.error || `Erro ${resp.status}`);
  }
  return data;
}

export const sendMessage = (text) => postJSON("/api/message", { text });
export const setSettings = (settings) => postJSON("/api/settings", settings);
export const getState = () => getJSON("/api/state");
export const newChat = () => postJSON("/api/new", {});
export const listDbs = () => getJSON("/api/dbs");

// URLs para baixar/exibir artefatos: o backend resolve pelo basename.
export const csvUrl = (path) =>
  `/api/csv?name=${encodeURIComponent(basename(path))}`;
export const chartUrl = (path) =>
  `/api/chart?name=${encodeURIComponent(basename(path))}`;

function basename(p) {
  if (!p) return "";
  return p.split(/[\\/]/).pop();
}
