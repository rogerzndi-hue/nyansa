import { api } from "./api.js";
import { clear, h, svgEl } from "./dom.js";
import { LEVELS, levelOf } from "./levels.js";
import { ATTRIBUTION } from "./mercator.js";
import { createMap } from "./map.js";
import { BORDERS_ATTRIBUTION, loadBordersProgressive } from "./borders.js";

const EV_PAGE = 40;
const state = { alerts: [], alertsExternal: false, showAcked: false, risk: [], events: [], meta: null, region: "", domain: "", channel: "", evLimit: EV_PAGE, sig: "", selected: null, byIso: {}, mode: "sat", map: null, borders: undefined, tileNote: null };
const $ = (id) => document.getElementById(id);
const fmtDate = (iso) => new Date(iso).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" });

const scoreOf = (r) => (state.domain ? r.domain_scores[state.domain] ?? 0 : r.score);
const visible = (r) => !state.region || r.region === state.region;
const isSocial = (src) => src.channel === "social";
/** Canal d'un événement : "media", "social" (réseaux sociaux seuls : non confirmé) ou "mixed". */
function channelOf(e) {
  const srcs = e.sources ?? [];
  const social = srcs.filter(isSocial).length;
  return social === 0 ? "media" : social === srcs.length ? "social" : "mixed";
}
const inScope = (e) => (!state.region || e.region === state.region) && (!state.domain || e.domain === state.domain)
  && (!state.channel || (state.channel === "social" ? channelOf(e) !== "media" : channelOf(e) !== "social"));
const shortName = (name) => { const n = String(name ?? "").split(" (")[0]; return n.length > 22 ? n.slice(0, 21) + "…" : n; };

/** Un bouton par source : article (médias) ou publication (réseaux sociaux). Liens filtrés http/https côté serveur. */
function sourceLinks(sources) {
  const links = (sources ?? []).filter((s) => s.url);
  if (!links.length) return h("span", { class: "muted", title: "Pas de lien disponible" }, "–");
  return h("span", { class: "links" }, links.map((s) => {
    const social = isSocial(s);
    const verb = social ? "Voir la publication" : "Lire l'article";
    const label = links.length > 1 ? shortName(s.source_name) : social ? "Post" : "Lire";
    return h("a", {
      class: social ? "btn btn-social" : "btn", href: s.url, target: "_blank", rel: "noopener noreferrer",
      title: `${verb} — ${s.source_name ?? s.source_id}${social ? " (non vérifiée)" : ""}`,
      "aria-label": `${verb} : ${s.source_name ?? s.source_id}`,
    }, `${label} ↗`);
  }));
}
const CHANNEL_TAGS = {
  social: ["tag warn", "Réseaux sociaux seuls · non confirmé"],
  mixed: ["tag", "Médias + réseaux sociaux"],
};
const channelTag = (e) => { const t = CHANNEL_TAGS[channelOf(e)]; return t ? h("span", { class: t[0] }, t[1]) : null; };

const fmtDateTime = (iso) => new Date(iso).toLocaleString("fr-FR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

function renderAlerts() {
  const open = state.alerts.filter((a) => a.status === "new");
  const rank = { critical: 3, high: 2, moderate: 1, low: 0 };
  const shown = state.alerts.filter((a) => state.showAcked || a.status === "new")
    .sort((a, b) => (a.status === "new") !== (b.status === "new") ? (a.status === "new" ? -1 : 1)
      : rank[b.level] - rank[a.level] || b.created_at.localeCompare(a.created_at)) // à traiter d'abord, puis gravité, puis récence
    .slice(0, 30);
  $("al-sub").textContent = `· ${open.length} à traiter`;
  const list = $("al-list");
  clear(list);
  if (!shown.length) list.append(h("li", { class: "muted" }, "Aucune alerte à traiter."));
  for (const a of shown) {
    const unconfirmed = a.verification === "unconfirmed_social";
    list.append(h("li", { class: `al-item lv-${a.level}${a.status === "ack" ? " ack" : ""}` },
      badge(a.level, a.level_label),
      h("div", {},
        h("div", { class: "al-title" }, `${a.country_name} — ${a.title}`),
        h("div", { class: "muted small" }, `${a.domain_label} · détectée ${fmtDateTime(a.created_at)} · règle « ${a.rule_name} »`),
        a.reason === "escalation" ? h("span", { class: "tag warn" }, "Escalade : niveau relevé") : null,
        h("span", { class: unconfirmed ? "tag warn" : "tag" }, a.verification_label)),
      h("div", { class: "al-act" },
        sourceLinks(a.sources.map((s) => ({ ...s, source_name: s.name }))),
        h("a", { class: "muted small", href: a.map_url, target: "_blank", rel: "noopener noreferrer", title: "Recherche du pays sur OpenStreetMap : localisation approximative" }, "Carte ↗"),
        a.status === "new"
          ? h("button", { type: "button", class: "btn ack", onclick: (e) => ackAlert(a.id, e.currentTarget) }, "Acquitter")
          : h("span", { class: "muted small" }, `Acquittée par ${a.acked_by}`))));
  }
  $("al-note").textContent = state.alertsExternal
    ? "Notifications externes (webhook, e-mail) actives selon les règles : plage de silence, limite horaire, jamais de répétition pour un même incident."
    : "Mode démonstration : aucune notification externe n'est envoyée.";
}

async function ackAlert(id, button) {
  button.disabled = true;
  try {
    const done = await api.ackAlert(id);
    state.alerts = state.alerts.map((a) => (a.id === done.id ? done : a));
    renderAlerts();
  } catch (err) {
    button.disabled = false;
    button.title = err.message;
    button.textContent = "Échec : réessayer";
  }
}

async function renderRules() {
  const ul = $("al-rules-list");
  try {
    const rules = await api.alertRules();
    clear(ul);
    for (const r of rules) {
      const crit = [`niveau ≥ ${r.min_level}`, r.region && `région ${r.region}`, r.countries.length && `pays ${r.countries.join(", ")}`,
        r.domains.length && `thèmes ${r.domains.join(", ")}`, r.keywords.length && `mots-clés ${r.keywords.join(", ")}`,
        r.include_social ? "réseaux sociaux inclus (non confirmés)" : "médias seulement",
        r.webhook_host && `webhook ${r.webhook_host}`, r.emails && `${r.emails} e-mail(s)`, r.quiet_hours && `silence ${r.quiet_hours}`].filter(Boolean);
      ul.append(h("li", {}, h("b", {}, r.name), r.active ? "" : " (inactive)", " : ", crit.join(" ; ")));
    }
  } catch { /* règles non disponibles : l'onglet reste vide */ }
}

const badge = (level, label) => h("span", { class: `badge lv-${level}` }, label);

function renderFilters() {
  const region = $("f-region"), domain = $("f-domain");
  region.append(h("option", { value: "" }, "Toute l'Afrique"));
  for (const [k, v] of Object.entries(state.meta.regions)) region.append(h("option", { value: k }, v));
  domain.append(h("option", { value: "" }, "Tous les thèmes"));
  for (const [k, v] of Object.entries(state.meta.domains)) domain.append(h("option", { value: k }, v));
  region.addEventListener("change", () => { state.region = region.value; render(); });
  domain.addEventListener("change", () => { state.domain = domain.value; render(); });
  const channel = $("f-channel");
  for (const [k, v] of [["", "Toutes les sources"], ["media", "Médias"], ["social", "Réseaux sociaux"]]) channel.append(h("option", { value: k }, v));
  channel.addEventListener("change", () => { state.channel = channel.value; render(); });
}

function renderKpis() {
  const rows = state.risk.filter(visible);
  const withEvents = rows.filter((r) => r.event_count > 0).length;
  const evs = state.events.filter(inScope);
  const critical = evs.filter((e) => e.level === "critical" || e.level === "high").length;
  const top = [...rows].filter((r) => r.event_count > 0).sort((a, b) => scoreOf(b) - scoreOf(a))[0];
  const kpi = (value, label) => h("div", { class: "kpi" }, h("b", {}, String(value)), h("span", { class: "muted" }, label));
  clear($("kpis"));
  $("kpis").append(
    kpi(rows.length, "pays suivis"),
    kpi(evs.length, "événements"),
    kpi(critical, "événements élevés ou critiques"),
    kpi(`${withEvents}/${rows.length}`, "pays avec données"),
    kpi(top ? top.country_name : "–", "score le plus haut"),
  );
}

function renderLegend() {
  clear($("legend"));
  for (const l of LEVELS) $("legend").append(h("li", {}, h("i", { class: `lv-${l.key}` }), l.label));
  $("legend").append(h("li", {}, h("i", { class: "lv-none" }), "Aucune donnée"));
}

function renderMap() {
  const svg = $("map");
  const sat = state.mode === "sat";
  for (const [id, on] of [["mode-sat", sat], ["mode-map", !sat]]) $(id).setAttribute("aria-pressed", String(on));
  const tip = document.querySelector(".tip") || document.body.appendChild(h("div", { class: "tip", hidden: true }));
  state.map ??= createMap(svg, {
    onSelect: (iso) => select(iso),
    onNote: (msg) => { state.tileNote = msg; updateNote(); },
    tip,
    basemap: state.mode,
  });
  state.map.setBasemap(state.mode);
  if (state.borders) state.map.setBorders(state.borders);
  state.map.update(state.risk.map((r) => {
    const score = scoreOf(r);
    const label = r.event_count ? Math.round(score) : "aucune donnée";
    return { iso3: r.iso3, name: r.country_name, level: r.event_count ? levelOf(score) : "none", visible: visible(r),
      selected: state.selected === r.iso3, aria: `${r.country_name} : ${label}`, tip: `${r.country_name} · ${label}` };
  }));
  $("map-sub").textContent = state.domain ? `· ${state.meta.domains[state.domain]}` : "· score global";
  updateNote();
}

function updateNote() {
  const sat = state.mode === "sat";
  const parts = [];
  if (sat) parts.push(state.tileNote ?? ATTRIBUTION);
  parts.push(state.borders ? BORDERS_ATTRIBUTION : state.borders === null
    ? "Frontières indisponibles (réseau ?) : un point par pays est affiché à la place."
    : "Chargement des frontières…");
  $("map-note").textContent = parts.join(" · ");
}

function renderDetail() {
  const el = $("detail");
  clear(el);
  const r = state.byIso[state.selected];
  if (!r) { el.append(h("h2", {}, "Pays"), h("p", { class: "muted" }, "Sélectionnez un pays sur la carte ou dans le classement.")); return; }
  el.append(
    h("h2", {}, r.country_name, " ", badge(r.event_count ? r.level : "none", r.event_count ? r.level_label : "Aucune donnée")),
    h("div", { class: "score" }, String(Math.round(r.score)), h("span", { class: "muted small" }, " / 100")),
    h("p", { class: "muted small" }, `Tendance 30 j : ${r.trend_30d >= 0 ? "+" : ""}${Math.round(r.trend_30d)} · confiance ${({ low: 'faible', medium: 'moyenne', high: 'élevée' })[r.confidence] ?? r.confidence} · ${r.source_count} source(s), ${r.language_count} langue(s)`),
  );
  for (const [d, label] of Object.entries(state.meta.domains)) {
    const v = r.domain_scores[d] ?? 0;
    el.append(h("div", { class: "row" }, h("span", {}, label), h("div", { class: "bar" }, h("div", { style: `width:${Math.min(v, 100)}%` })), h("span", { class: "muted" }, String(Math.round(v)))));
  }
  if (r.notes.length) el.append(h("ul", { class: "notes" }, r.notes.map((n) => h("li", {}, n))));
  el.append(h("h2", {}, "Contributions au score"));
  if (!r.contributions.length) el.append(h("p", { class: "muted" }, "Aucun événement dans la fenêtre d'analyse."));
  for (const c of r.contributions) {
    const ev = state.events.find((e) => e.id === c.event_id);
    el.append(h("div", { class: "row contrib" }, h("span", {}, c.title), sourceLinks(ev?.sources), h("span", { class: "muted" }, `+${c.points.toFixed(1)}`)));
  }
}

function renderEvents() {
  const evs = state.events.filter(inScope);
  const t = $("events");
  clear(t);
  const shown = evs.slice(0, state.evLimit);
  t.append(
    h("thead", {}, h("tr", {}, ["Date", "Pays", "Événement", "Thème", "Fiabilité", "Criticité", "Lire"].map((x) => h("th", {}, x)))),
    h("tbody", {}, shown.map((e) => h("tr", { "data-country": e.country },
      h("td", {}, fmtDate(e.occurred_at)), h("td", {}, state.byIso[e.country]?.country_name ?? e.country),
      h("td", {}, e.title, channelTag(e), e.evidence?.length ? h("small", { class: "muted evid" }, `Indices : ${e.evidence.join(", ")}`) : null),
      h("td", {}, state.meta.domains[e.domain]), h("td", {}, `${e.admiralty} · ${e.source_count} src`),
      h("td", {}, badge(e.level, `${e.criticality} ${e.level_label}`)), h("td", {}, sourceLinks(e.sources), h("a", { class: "btn btn-flash", href: `deliverables.html?new=flash_alert&country=${encodeURIComponent(e.country)}&start=${encodeURIComponent(String(e.occurred_at).slice(0, 10))}`, title: "Préparer une alerte flash à partir de cet événement" }, "Flash"))))),
  );
  $("ev-sub").textContent = `· ${shown.length < evs.length ? `${shown.length} sur ${evs.length}` : evs.length}`;
  const more = $("ev-more-wrap");
  clear(more);
  if (shown.length < evs.length) {
    more.append(h("button", { type: "button", class: "btn more", onclick: () => { state.evLimit += EV_PAGE; renderEvents(); } },
      `Afficher ${Math.min(EV_PAGE, evs.length - shown.length)} de plus`));
  }
}

function renderRanking() {
  const ol = $("ranking");
  clear(ol);
  const rows = state.risk.filter(visible).sort((a, b) => scoreOf(b) - scoreOf(a));
  for (const r of rows) {
    ol.append(h("li", { onclick: () => select(r.iso3, { focus: true }) }, h("span", {}, r.country_name),
      badge(r.event_count ? levelOf(scoreOf(r)) : "none", r.event_count ? String(Math.round(scoreOf(r))) : "–")));
  }
}

const REASON_LABELS = { no_country: "sans pays reconnu", ambiguous_country: "pays ambigu", foreign_focus: "titre sur un pays hors Afrique", stale: "ancien article republié", no_domain: "sans thème reconnu" };
const reasonText = (reasons) => {
  const parts = Object.entries(reasons ?? {}).map(([k, v]) => `${v} ${REASON_LABELS[k] ?? k}`);
  return parts.length ? ` (${parts.join(", ")})` : "";
};

// Cumul en base + dernier cycle : après le 2e cycle tout est « déjà connu », le dernier cycle seul afficherait « 0 rattachés ».
const sourceStatus = (r) => {
  const fresh = r.documents - (r.duplicates ?? 0);
  const parts = [`${r.total ?? r.documents} documents en base (${r.total_routed ?? r.routed} rattachés à un pays/thème, ${r.total_unrouted ?? r.unrouted} à qualifier)`,
    `dernière collecte : ${r.documents} lus, ${fresh} nouveaux`];
  if (r.skipped) parts.push(`${r.skipped} ignorés (sans titre ni texte)`);
  if (!r.documents) parts.push("flux vide : aucun élément renvoyé");
  return parts.join(" ; ") + (fresh > 0 ? reasonText(r.reasons) : "");
};

async function renderUnrouted(el) {
  let items;
  try { items = await api.unrouted(); } catch { return; }
  if (!items.length) return;
  el.append(h("details", {}, h("summary", {}, `Derniers articles à qualifier (10 par source au plus ; ${items.length})`),
    h("ul", {}, items.map((d) => h("li", {}, `[${d.source_id}] ${d.title} — `, h("span", { class: "muted" }, REASON_LABELS[d.reason] ?? d.reason))))));
}

async function renderSources() {
  const el = $("sources");
  clear(el);
  let info;
  try { info = await api.sources(); } catch { return; }
  if (!info.sources) { el.append("Mode démonstration : aucune source réelle connectée."); return; }
  const reports = Object.fromEntries(info.reports.map((r) => [r.source_id, r]));
  el.append(h("b", {}, "Sources"), h("ul", {}, info.sources.filter((s) => s.kind === "rss").map((s) => {
    const r = reports[s.id];
    const status = !s.active ? "inactive" : !r ? "collecte en cours…"
      : r.ok ? sourceStatus(r)
      : null;
    return h("li", {}, `${s.name} (fiabilité ${s.reliability}${s.channel === "social" ? ", réseau social" : ""}) : `, status ?? h("span", { class: "err" }, `échec — ${r.error}`));
  })));
  if (state.meta && !state.meta.demo) await renderUnrouted(el);
  const waiting = info.sources.some((s) => s.active && s.kind === "rss" && !reports[s.id]);
  if (waiting) setTimeout(refreshAll, 5000);
}

async function refreshAll() {
  try {
    const [risk, events, al] = await Promise.all([api.risk(), api.events({ limit: 200 }), api.alerts({ limit: 100 }).catch(() => state.alertsData)]);
    const sig = JSON.stringify([risk, events, al]);
    if (sig !== state.sig) { // rien n'a changé : on évite de reconstruire carte, tableau et classement
      Object.assign(state, { sig, risk, events, alertsData: al, alerts: al?.alerts ?? [], alertsExternal: !!al?.external, byIso: Object.fromEntries(risk.map((r) => [r.iso3, r])) });
      render();
    }
  } catch { /* on garde l'affichage actuel */ }
  renderSources();
}

function setMode(mode) { state.mode = mode; renderMap(); }

function select(iso3, { focus = false } = {}) {
  state.selected = iso3;
  renderMap(); renderDetail(); // le reste (KPI, tableau, classement) ne dépend pas de la sélection
  if (focus && state.map) state.map.focus(iso3);
}
function render() { renderAlerts(); state.evLimit = Math.max(EV_PAGE, Math.min(state.evLimit, 400)); renderKpis(); renderMap(); renderDetail(); renderEvents(); renderRanking(); }

/** Vue partageable : ?region=WEST&domain=SEC#MLI (valeurs inconnues ignorées). */
function applyUrlState() {
  const q = new URLSearchParams(location.search);
  if (q.get("region") in state.meta.regions) { state.region = q.get("region"); $("f-region").value = state.region; }
  if (q.get("domain") in state.meta.domains) { state.domain = q.get("domain"); $("f-domain").value = state.domain; }
  if (["media", "social"].includes(q.get("channel"))) { state.channel = q.get("channel"); $("f-channel").value = state.channel; }
  if (q.get("map") === "map") state.mode = "map";
  const iso = location.hash.slice(1).toUpperCase();
  if (iso in state.byIso) state.selected = iso;
}

async function main() {
  try {
    const [meta, risk, events, al] = await Promise.all([api.meta(), api.risk(), api.events({ limit: 200 }), api.alerts({ limit: 100 }).catch(() => null)]);
    Object.assign(state, { meta, risk, events, alertsData: al, alerts: al?.alerts ?? [], alertsExternal: !!al?.external, byIso: Object.fromEntries(risk.map((r) => [r.iso3, r])) });
    const banner = $("demo-banner");
    banner.hidden = false;
    banner.textContent = meta.mode === "live"
      ? "Sources réelles : CGU à valider avant toute exploitation durable"
      : "Données fictives de démonstration";
    renderFilters(); applyUrlState(); renderLegend();
    $("events").addEventListener("click", (e) => { // délégation : un seul écouteur ; les liens gardent leur comportement
      const row = e.target.closest("tr[data-country]");
      if (row && !e.target.closest("a")) select(row.dataset.country, { focus: true });
    });
    $("mode-sat").addEventListener("click", () => setMode("sat"));
    $("mode-map").addEventListener("click", () => setMode("map"));
    $("zoom-in").addEventListener("click", () => state.map?.zoomBy(1.6));
    $("zoom-out").addEventListener("click", () => state.map?.zoomBy(1 / 1.6));
    $("zoom-reset").addEventListener("click", () => state.map?.reset());
    render();
    loadBordersProgressive((b) => { state.borders = b; state.map?.setBorders(b); updateNote(); }) // 110 m puis 50 m
      .then((level) => { if (!level) { state.borders = null; updateNote(); } });
    renderSources();
    state.sig = JSON.stringify([risk, events, al]);
    $("al-all").addEventListener("change", (e) => { state.showAcked = e.target.checked; renderAlerts(); });
    renderRules();
    if (!meta.demo) { // pas de rafraîchissement en arrière-plan : onglet masqué = aucun appel
      setInterval(() => { if (!document.hidden) refreshAll(); }, 60_000);
      document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshAll(); });
    }
  } catch (err) {
    document.querySelector("main").replaceChildren(h("p", { class: "card" }, `Impossible de charger les données : ${err.message}`));
  }
}
main();
