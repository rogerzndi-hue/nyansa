// Identité DÉCLARATIVE du poste local (pas d'authentification) : envoyée à chaque appel. Le nom est encodé
// (un en-tête HTTP n'accepte pas tous les caractères) et décodé par le serveur.
let identity = { user: "", role: "analyst" };
export const setIdentity = (next) => { identity = { ...identity, ...next }; };
const idHeaders = () => ({ "X-Dev-Role": identity.role, ...(identity.user ? { "X-Dev-User": encodeURIComponent(identity.user) } : {}) });

async function request(method, path, body) {
  const headers = { Accept: "application/json", ...idHeaders(), ...(body === undefined ? {} : { "Content-Type": "application/json", "X-Nyansa": "1" }) };
  const r = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.detail ?? `${path} : HTTP ${r.status}`), { status: r.status, issues: data.issues ?? [] });
  return data;
}
const getJson = (path) => request("GET", path);
const seg = encodeURIComponent;

export const api = {
  meta: () => getJson("api/meta"),
  risk: () => getJson("api/risk/countries"),
  events: (params = {}) => getJson("api/events?" + new URLSearchParams(params)),
  sources: () => getJson("api/sources"),
  alerts: (params = {}) => getJson("api/alerts?" + new URLSearchParams(params)),
  alertRules: () => getJson("api/alerts/rules"),
  ackAlert: (id) => request("POST", `api/alerts/${seg(id)}/ack`, {}),
  unrouted: () => getJson("api/unrouted"),
  coverage: () => getJson("api/coverage"),
  // réseaux sociaux
  social: (params = {}) => getJson("api/social?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v))),
  addWatch: (body) => request("POST", "api/social/watches", body),
  toggleWatch: (id, active) => request("POST", `api/social/watches/${seg(id)}/toggle`, { active }),
  deleteWatch: (id) => request("POST", `api/social/watches/${seg(id)}/delete`, {}),
  // livrables
  catalog: (lang = "fr") => getJson(`api/deliverables/catalog?lang=${seg(lang)}`),
  capabilities: () => getJson("api/deliverables/capabilities"),
  reports: (params = {}) => getJson("api/deliverables?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v))),
  report: (id) => getJson(`api/deliverables/${seg(id)}`),
  createReport: (body) => request("POST", "api/deliverables", body),
  updateReport: (id, body) => request("PUT", `api/deliverables/${seg(id)}`, body),
  act: (id, action, body = {}) => request("POST", `api/deliverables/${seg(id)}/${action}`, body),
  check: (id) => getJson(`api/deliverables/${seg(id)}/check`),
  versions: (id) => getJson(`api/deliverables/${seg(id)}/versions`),
  diff: (id, a, b) => getJson(`api/deliverables/${seg(id)}/diff?a=${a}&b=${b}`),
  /** Export : récupéré avec l'identité (une simple navigation n'enverrait pas les en-têtes) puis remis au navigateur. */
  async exportBlob(id, fmt) {
    const r = await fetch(`api/deliverables/${seg(id)}/export.${fmt}`, { headers: idHeaders() });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail ?? `Export ${fmt} : HTTP ${r.status}`);
    }
    const name = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") ?? "")?.[1] ?? `livrable.${fmt}`;
    return { blob: await r.blob(), name };
  },
};
