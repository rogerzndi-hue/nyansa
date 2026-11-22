// Page « Réseaux sociaux » : veilles (hashtag, compte public, mot-clé) et suivi des publications détectées.
// Tout est construit avec h() (jamais innerHTML) : les publications viennent de sources externes.
// Les droits sont contrôlés par le serveur ; l'interface désactive seulement ce que le rôle ne peut pas faire.

import { api, setIdentity } from "./api.js";
import { clear, h } from "./dom.js";

const $ = (id) => document.getElementById(id);
const ROLES = { analyst: "Analyste", validator: "Validateur", manager: "Responsable de veille", executive: "Décideur (lecture)", admin: "Administrateur" };
const WRITE = new Set(["analyst", "manager", "admin"]);
const STATUS = { corroborated: ["tag ok", "Corroboré par un média"], unconfirmed: ["tag warn", "NON CONFIRMÉ"], to_qualify: ["tag muted", "À qualifier"] };
const STATUS_FILTER = { relevant: "Pertinentes (hors à qualifier)", corroborated: "Corroborés par un média", unconfirmed: "Non confirmés", to_qualify: "À qualifier" };
const CONNECTOR = { pending: "collecte en attente", ok: "flux OK", error: "échec du flux", inactive: "inactive", filter: "filtre, sans collecte", demo: "démonstration, sans collecte" };
const KIND = { hashtag: "Hashtag", account: "Compte public", channel: "Chaîne", keyword: "Mot-clé" };
const REASONS = { no_country: "pays non reconnu", ambiguous_country: "pays ambigu", foreign_focus: "titre sur un pays hors Afrique", stale: "ancien article republié", no_domain: "thème non reconnu" };
const KIND_FORM = {
  "mastodon:hashtag": { label: "Hashtag", placeholder: "Mali", instance: true, help: "Collecte les publications publiques de ce hashtag (flux RSS de l'instance)." },
  "mastodon:account": { label: "Compte (nom ou nom@instance)", placeholder: "ministere@piaille.fr", instance: true, help: "Collecte un compte PUBLIC d'institution ou de média. Évitez les comptes de particuliers." },
  "bluesky:account": { label: "Compte Bluesky (nom complet ou did:plc:…)", placeholder: "media.bsky.social", instance: false, help: "Collecte le flux RSS public d'un compte d'institution ou de média. Les hashtags Bluesky ne sont pas collectables (la recherche peut exiger un compte). Non vérifié en réel." },
  "youtube:channel": { label: "Chaîne (identifiant UC… ou adresse youtube.com/channel/UC…)", placeholder: "UCxxxxxxxxxxxxxxxxxxxxxx", instance: false, help: "Collecte les dernières vidéos d'une chaîne (flux officiel). Titre et description seulement, pas la vidéo. Non vérifié en réel." },
  "reddit:channel": { label: "Subreddit", placeholder: "Nigeria", instance: false, help: "Collecte les publications d'un subreddit. Les CGU de Reddit limitent l'usage professionnel : à valider. Non vérifié en réel." },
  keyword: { label: "Mot ou expression", placeholder: "coup d'État", instance: false, help: "Ne collecte rien : repère ce mot dans les publications déjà collectées." },
};

const state = { data: null, meta: null, risk: [], who: "", role: "analyst", timer: 0 };
const httpUrl = (u) => (typeof u === "string" && /^https?:\/\//.test(u) ? u : null);
const fmt = (iso) => (iso ? new Date(iso).toLocaleString("fr-FR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "–");
const shortName = (n) => { const s = String(n ?? "").split(" (")[0]; return s.length > 24 ? s.slice(0, 23) + "…" : s; };
const canWrite = () => WRITE.has(state.role);

const prefs = {
  read() { try { return JSON.parse(localStorage.getItem("nyansa.identity") ?? "{}"); } catch { return {}; } },
  write(v) { try { localStorage.setItem("nyansa.identity", JSON.stringify(v)); } catch { /* stockage indisponible : sans effet */ } },
};
function say(text, ok = false) {
  const m = $("msg");
  m.textContent = text;
  m.hidden = !text;
  m.classList.toggle("ok", ok);
}
async function run(fn) {
  say("");
  try { return await fn(); } catch (err) { say(err.message); return null; }
}

function initIdentity() {
  const saved = prefs.read();
  state.who = saved.user ?? "";
  state.role = saved.role in ROLES ? saved.role : "analyst";
  const role = $("role");
  for (const [k, v] of Object.entries(ROLES)) role.append(h("option", { value: k, selected: k === state.role }, v));
  $("who").value = state.who;
  const apply = () => {
    state.who = $("who").value.trim();
    state.role = role.value;
    prefs.write({ user: state.who, role: state.role });
    setIdentity({ user: state.who, role: state.role });
    renderWatches();
    syncForm();
  };
  $("who").addEventListener("change", apply);
  role.addEventListener("change", apply);
  setIdentity({ user: state.who, role: state.role });
}

// --- formulaire ---------------------------------------------------------------------------------------------
function syncForm() {
  const f = KIND_FORM[$("w-kind").value];
  $("w-value-label").textContent = f.label;
  $("w-value").placeholder = f.placeholder;
  $("w-instance-wrap").hidden = !f.instance;
  $("w-help").textContent = canWrite() ? f.help : "Votre rôle ne permet pas de gérer les veilles (analyste, responsable de veille ou admin).";
  for (const el of $("w-form").elements) el.disabled = !canWrite();
}

function initForm() {
  const countries = $("w-countries");
  for (const r of [...state.risk].sort((a, b) => a.country_name.localeCompare(b.country_name, "fr"))) countries.append(h("option", { value: r.iso3 }, r.country_name));
  const domains = $("w-domains");
  for (const [k, v] of Object.entries(state.meta.domains)) domains.append(h("label", { class: "small" }, h("input", { type: "checkbox", value: k }), v));
  $("w-kind").addEventListener("change", syncForm);
  $("w-form").addEventListener("submit", (e) => { e.preventDefault(); run(addWatch); });
  syncForm();
}

async function addWatch() {
  const choice = $("w-kind").value;
  const [platform, kind] = choice.includes(":") ? choice.split(":") : [undefined, choice];
  const body = {
    kind, platform, value: $("w-value").value, name: $("w-name").value.trim() || undefined,
    countries: [...$("w-countries").selectedOptions].map((o) => o.value),
    categories: [...$("w-domains").querySelectorAll("input:checked")].map((i) => i.value),
  };
  if (KIND_FORM[choice].instance) body.instance = $("w-instance").value.trim();
  const w = await api.addWatch(body);
  $("w-form").reset();
  $("w-instance").value = "mastodon.social";
  syncForm();
  await refresh();
  const live = w.collectable && !state.data.demo;
  say(live ? "Veille ajoutée : collecte lancée, premiers résultats dans quelques secondes."
    : w.collectable ? "Veille ajoutée (démonstration : aucune collecte réelle)." : "Veille ajoutée.", true);
  if (live) for (const ms of [4000, 12000]) setTimeout(() => run(refresh), ms);
}

// --- rendu --------------------------------------------------------------------------------------------------
function renderKpis() {
  const s = state.data.summary;
  const kpi = (v, l) => h("div", { class: "kpi" }, h("b", {}, String(v)), h("span", { class: "muted" }, l));
  clear($("s-kpis"));
  $("s-kpis").append(kpi(s.posts_24h, "publications (24 h)"), kpi(s.corroborated, "corroborées par un média"), kpi(s.unconfirmed, "non confirmées"), kpi(s.to_qualify, "à qualifier"));
}

// « flux OK » seul ne dit pas si quelque chose a été lu : on donne les chiffres de la dernière collecte.
function connectorDetail(c) {
  if (!c.documents && !c.total) return "Flux lu, mais aucune publication renvoyée (sujet peu actif, ou flux non fourni par cette source).";
  const parts = [`dernière collecte : ${c.documents ?? 0} élément(s) lu(s)`, `${c.total ?? 0} en base`];
  if (c.skipped) parts.push(`${c.skipped} ignoré(s) (ni titre ni texte)`);
  return parts.join(" ; ");
}

function renderWatches() {
  if (!state.data) return;
  const list = $("w-list");
  clear(list);
  $("w-sub").textContent = `· ${state.data.watches.length}`;
  if (!state.data.watches.length) list.append(h("li", { class: "muted" }, "Aucune veille. Ajoutez un hashtag, un compte public, une chaîne ou un mot-clé."));
  for (const w of state.data.watches) {
    const c = w.connector;
    const scope = [w.countries.length && `pays ${w.countries.join(", ")}`, w.categories.length && `catégories ${w.categories.join(", ")}`].filter(Boolean).join(" ; ");
    list.append(h("li", { class: `wt-item${w.active ? "" : " off"}` },
      h("div", { class: "sp-row" }, h("b", {}, w.name), h("span", { class: "tag" }, w.platform_label ? `${w.platform_label} · ${KIND[w.kind]}` : KIND[w.kind]),
        h("span", { class: c.status === "error" ? "tag warn" : c.status === "ok" ? "tag ok" : "tag muted" }, CONNECTOR[c.status] ?? c.status)),
      h("div", { class: "muted small" }, `${w.posts_24h} publication(s) en 24 h · ${w.posts_7d} en 7 j · dernière : ${fmt(w.last_post_at)}`),
      scope ? h("div", { class: "muted small" }, `Filtre : ${scope}`) : null,
      c.status === "error" ? h("div", { class: "err small" }, `Échec : ${c.error ?? "inconnu"}`) : null,
      c.status === "ok" ? h("div", { class: c.documents || c.total ? "muted small" : "warn-text small" }, connectorDetail(c)) : null,
      h("div", { class: "sp-row" },
        h("button", { type: "button", class: "btn", disabled: !canWrite(), onclick: () => run(async () => { await api.toggleWatch(w.id, !w.active); await refresh(); }) }, w.active ? "Mettre en pause" : "Reprendre"),
        h("button", { type: "button", class: "btn", disabled: !canWrite(), onclick: () => { if (confirm(`Supprimer la veille « ${w.name} » ? Les publications déjà collectées restent consultables.`)) run(async () => { await api.deleteWatch(w.id); await refresh(); }); } }, "Supprimer"))));
  }
  const sel = $("f-watch"), keep = sel.value;
  clear(sel);
  sel.append(h("option", { value: "" }, "Toutes"));
  for (const w of state.data.watches) sel.append(h("option", { value: w.id, selected: w.id === keep }, w.name));
}

function mediaButtons(post) {
  return post.media_sources.filter((m) => httpUrl(m.url)).map((m) =>
    h("a", { class: "btn", href: m.url, target: "_blank", rel: "noopener noreferrer", title: `Lire l'article — ${m.name} (fiabilité ${m.reliability})` }, `Lire · ${shortName(m.name)} ↗`));
}

function renderPosts() {
  const names = Object.fromEntries(state.data.watches.map((w) => [w.id, w.name]));
  const list = $("p-list");
  clear(list);
  const d = state.data;
  $("p-sub").textContent = `· ${d.posts.length < d.total ? `${d.posts.length} sur ${d.total}` : d.total}`;
  const hidden = $("f-status").value === "relevant" ? d.by_status?.to_qualify ?? 0 : 0;
  if (hidden) list.append(h("li", { class: "muted small" }, `${hidden} publication(s) « à qualifier » masquée(s) : hors sujet, ou pays/thème non reconnu. Choisissez « Tous » ou « À qualifier » dans le filtre Statut pour les voir.`));
  if (!d.posts.length) list.append(h("li", { class: "muted" }, d.watches.length || hidden ? "Aucune publication pertinente pour ces critères." : "Ajoutez une veille pour commencer."));
  for (const p of d.posts) {
    const [cls, label] = STATUS[p.status];
    list.append(h("li", { class: `sp-item ${p.status}` },
      h("div", { class: "sp-row" },
        h("span", { class: cls, title: p.status_label }, label),
        p.level ? h("span", { class: `badge lv-${p.level}` }, p.level_label) : null,
        p.country_name ? h("span", { class: "muted small" }, `${p.country_name} · ${p.domain_label}`) : h("span", { class: "muted small" }, `Raison : ${REASONS[p.reason] ?? p.reason ?? "non classée"}`),
        h("span", { class: "muted small" }, `${fmt(p.published_at)} · ${p.source_name}`)),
      h("div", { class: "sp-title" }, p.title),
      p.summary && p.summary !== p.title ? h("div", { class: "sp-sum" }, p.summary) : null,
      h("div", { class: "sp-row" },
        httpUrl(p.url) ? h("a", { class: "btn btn-social", href: p.url, target: "_blank", rel: "noopener noreferrer", title: "Voir la publication (non vérifiée)" }, "Voir la publication ↗") : null,
        mediaButtons(p),
        p.also_in?.length ? h("span", { class: "muted small" }, `aussi dans : ${p.also_in.join(", ")}`) : null,
        p.repeats ? h("span", { class: "muted small" }, `+ ${p.repeats} publication(s) quasi identique(s) du même flux`) : null,
        p.watches.map((id) => h("span", { class: "tag" }, names[id] ?? id)))));
  }
  $("s-limits").textContent = d.limits;
}

function render() { renderKpis(); renderWatches(); renderPosts(); }

async function refresh() {
  const params = { status: $("f-status").value, watch: $("f-watch").value };
  const data = await api.social(params);
  state.data = data;
  render();
}

async function main() {
  try {
    const [meta, risk] = await Promise.all([api.meta(), api.risk()]);
    Object.assign(state, { meta, risk });
    const banner = $("demo-banner");
    banner.hidden = false;
    banner.textContent = meta.mode === "live" ? "Sources réelles : CGU à valider avant toute exploitation durable" : "Données fictives de démonstration";
    const status = $("f-status");
    for (const [k, v] of Object.entries(STATUS_FILTER)) status.append(h("option", { value: k }, v));
    status.value = "relevant"; // par défaut : sans le bruit (musique, doublons, hors sujet) ; « Tous » reste disponible
    status.addEventListener("change", () => run(refresh));
    $("f-watch").addEventListener("change", () => run(refresh));
    initIdentity();
    initForm();
    await refresh();
    if (!meta.demo) { // pas de rafraîchissement en arrière-plan quand l'onglet est masqué
      setInterval(() => { if (!document.hidden) run(refresh); }, 60_000);
      document.addEventListener("visibilitychange", () => { if (!document.hidden) run(refresh); });
    }
  } catch (err) {
    document.querySelector("main").replaceChildren(h("p", { class: "card" }, `Impossible de charger les données : ${err.message}`));
  }
}
main();
