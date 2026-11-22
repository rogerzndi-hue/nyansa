// Page « Livrables » : formulaire de génération (25 modèles), éditeur par blocs, circuit de validation, exports.
// Tout est construit avec h() (jamais innerHTML) : titres et résumés viennent de sources externes.
// Les droits réels sont contrôlés par le serveur ; l'interface masque seulement ce que le rôle ne peut pas faire.

import { api, setIdentity } from "./api.js";
import { clear, h } from "./dom.js";

const $ = (id) => document.getElementById(id);
const STATUS = { draft: "Brouillon", in_review: "En validation", validated: "Validé", archived: "Archivé" };
const KIND = { fact: "Fait", computed: "Calcul", estimate: "Estimation", hypothesis: "Hypothèse" };
const EVIDENCE = { confirmed: "Confirmé (2 sources ou plus)", single_source: "Source unique", unconfirmed_social: "Réseaux sociaux seuls : non confirmé" };
const ROLES = { analyst: "Analyste", validator: "Validateur", manager: "Responsable de veille", executive: "Décideur (lecture)", admin: "Administrateur" };
const EDIT = new Set(["analyst", "admin"]);
const VALIDATE = new Set(["validator", "manager", "admin"]);
const SHAREABLE = new Set(["PUBLIC", "INTERNE"]); // CONFIDENTIEL et RESTREINT : pas de partage par message
const ANALYST_KINDS = ["fact", "estimate", "hypothesis"];

const state = { caps: null, meta: null, catalog: [], byId: {}, countries: [], rec: null, work: null, dirty: false, issues: [], who: "", role: "analyst", seq: 0 };
const fmtDate = (iso) => (iso ? new Date(iso).toLocaleString("fr-FR", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "");
const clone = (x) => JSON.parse(JSON.stringify(x));
const httpUrl = (u) => (typeof u === "string" && /^https?:\/\//.test(u) ? u : null);

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
  try { return await fn(); } catch (err) {
    state.issues = err.issues?.length ? err.issues : state.issues;
    say(err.message);
    if (err.issues?.length) renderEditor();
    return null;
  }
}
const canEdit = () => EDIT.has(state.role) && state.rec?.status === "draft";

// --- identité -------------------------------------------------------------------------------------------
function initIdentity() {
  const saved = prefs.read();
  state.who = saved.user ?? "";
  state.role = saved.role in ROLES ? saved.role : "analyst";
  const role = $("role");
  for (const [k, v] of Object.entries(ROLES)) role.append(h("option", { value: k, selected: k === state.role }, v));
  $("who").value = state.who;
  const apply = async () => {
    state.who = $("who").value.trim();
    state.role = role.value;
    prefs.write({ user: state.who, role: state.role });
    setIdentity({ user: state.who, role: state.role });
    $("n-analyst").value ||= state.who;
    await run(async () => { await refreshList(); if (state.rec) await openReport(state.rec.id, { keepIssues: true }); });
  };
  $("who").addEventListener("change", apply);
  role.addEventListener("change", apply);
  setIdentity({ user: state.who, role: state.role });
}

// --- formulaire de création -----------------------------------------------------------------------------
function renderForm() {
  const tpl = $("n-template");
  for (const t of state.catalog) tpl.append(h("option", { value: t.id }, `${t.number}. ${t.title}`));
  const countries = $("n-countries");
  for (const c of state.countries) countries.append(h("option", { value: c.iso3 }, c.country_name));
  const region = $("n-region");
  region.append(h("option", { value: "" }, "Toute l'Afrique"));
  for (const [k, v] of Object.entries(state.meta.regions)) region.append(h("option", { value: k }, v));
  for (const [k, v] of Object.entries(state.meta.domains)) {
    $("n-domains").append(h("label", { class: "inl" }, h("input", { type: "checkbox", value: k }), ` ${v}`));
  }
  tpl.addEventListener("change", syncTemplate);
  $("n-lang").addEventListener("change", async () => {
    const cat = await run(() => api.catalog($("n-lang").value));
    if (cat) { state.catalog = cat; state.byId = Object.fromEntries(cat.map((t) => [t.id, t])); const cur = tpl.value; clear(tpl); for (const t of cat) tpl.append(h("option", { value: t.id, selected: t.id === cur }, `${t.number}. ${t.title}`)); syncTemplate(); }
  });
  $("new-form").addEventListener("submit", (e) => { e.preventDefault(); createReport(); });
  $("n-analyst").value = state.who;
  syncTemplate();
}

function syncTemplate() {
  const t = state.byId[$("n-template").value];
  if (!t) return;
  const scope = { country: "Ce modèle demande au moins un pays.", region: "Ce modèle demande une région (ou des pays)." }[t.scope] ?? "Périmètre libre : tous les pays, ou ceux que vous choisissez.";
  $("n-scope").textContent = `${scope} Période par défaut : ${t.days} jour(s) avant la date de fin (aujourd'hui si vide).`;
  for (const box of $("n-domains").querySelectorAll("input")) box.checked = t.domains.includes(box.value);
  const sec = $("n-sections");
  clear(sec);
  for (const s of t.sections) {
    const locked = s.key === "sources";
    sec.append(h("label", { class: "inl" }, h("input", { type: "checkbox", value: s.key, checked: true, disabled: locked }), ` ${s.title}`, s.mode === "analyst" ? h("span", { class: "tag" }, "à rédiger") : null));
  }
}

function formParams() {
  const t = state.byId[$("n-template").value];
  const picked = [...$("n-sections").querySelectorAll("input:checked")].map((i) => i.value);
  const params = {
    template_id: t.id, language: $("n-lang").value, detail: $("n-detail").value,
    countries: [...$("n-countries").selectedOptions].map((o) => o.value), region: $("n-region").value || null,
    start: $("n-start").value || null, end: $("n-end").value ? `${$("n-end").value}T23:59:59` : null,
    domains: [...$("n-domains").querySelectorAll("input:checked")].map((i) => i.value),
    analyst: $("n-analyst").value.trim(), organization: $("n-org").value.trim(), recipient: $("n-recipient").value.trim(),
    classification: $("n-class").value, instructions: $("n-instr").value.trim(),
  };
  if (picked.length !== t.sections.length) params.sections = picked;
  return params;
}

async function createReport() {
  const rec = await run(() => api.createReport(formParams()));
  if (!rec) return;
  say("Brouillon généré.", true);
  await refreshList();
  await openReport(rec.id);
}

function applyQuery() {
  const q = new URLSearchParams(location.search);
  if (q.get("new") in state.byId) { $("n-template").value = q.get("new"); syncTemplate(); }
  const iso = (q.get("country") ?? "").toUpperCase();
  for (const o of $("n-countries").options) o.selected = o.value === iso;
  if (/^\d{4}-\d{2}-\d{2}$/.test(q.get("start") ?? "")) $("n-start").value = q.get("start");
}

// --- liste ----------------------------------------------------------------------------------------------
async function refreshList() {
  const rows = await api.reports({ status: $("l-status").value, q: $("l-q").value.trim() });
  const ul = $("list");
  clear(ul);
  $("list-sub").textContent = rows.length ? `· ${rows.length}` : "";
  if (!rows.length) ul.append(h("li", { class: "muted small" }, state.caps?.persistent ? "Aucun livrable." : "Aucun livrable (mode démo : conservés en mémoire seulement, perdus à l'arrêt du serveur)."));
  for (const r of rows) {
    ul.append(h("li", {}, h("button", { type: "button", class: `dl-item${state.rec?.id === r.id ? " sel" : ""}`, onclick: () => openReport(r.id) },
      h("b", {}, r.title),
      h("span", { class: "muted small" }, `${state.byId[r.template_id]?.title ?? r.template_id} · ${r.created_by} · ${fmtDate(r.updated_at)}`),
      h("span", {}, h("span", { class: `badge st-${r.status}` }, STATUS[r.status] ?? r.status), h("span", { class: "tag" }, `v${r.version} · ${r.classification}`), r.demo ? h("span", { class: "tag t-demo" }, "démo") : null))));
  }
}

async function openReport(id, { keepIssues = false } = {}) {
  if (state.dirty && state.rec?.id !== id && !confirm("Des modifications non enregistrées seront perdues. Continuer ?")) return;
  const rec = await run(() => api.report(id));
  if (!rec) { state.rec = null; state.work = null; renderEditor(); return; }
  state.rec = rec;
  state.work = clone(rec.sections);
  state.dirty = false;
  if (!keepIssues) state.issues = [];
  history.replaceState(null, "", `${location.pathname}${location.search}#${rec.id}`);
  renderEditor();
  refreshList().catch(() => {});
}

// --- éditeur --------------------------------------------------------------------------------------------
function withRefs(text) {
  return String(text).split(/(\[\d{1,4}\])/).map((part) => {
    const m = /^\[(\d{1,4})\]$/.exec(part);
    return m ? h("a", { href: `#src-${m[1]}`, class: "ref", onclick: (e) => { e.preventDefault(); $(`src-${m[1]}`)?.scrollIntoView({ block: "center" }); } }, part) : part;
  });
}

const touch = () => { state.dirty = true; const b = $("save-btn"); if (b) b.disabled = false; const d = $("dirty"); if (d) d.hidden = false; };
function rerender() { const y = window.scrollY; renderEditor(); window.scrollTo(0, y); }

function autoBlock(sec, b, editable) {
  const tag = h("span", { class: `tag t-${b.kind}` }, KIND[b.kind] ?? b.kind);
  const evid = b.status ? h("span", { class: `tag e-${b.status}` }, EVIDENCE[b.status] ?? b.status) : null;
  const remove = editable ? h("button", { type: "button", class: "btn small-btn", title: "Les blocs calculés ne se modifient pas ; ils se retirent",
    onclick: () => { sec.blocks = sec.blocks.filter((x) => x.id !== b.id); touch(); rerender(); } }, "Retirer") : null;
  if (b.kind === "heading") return h("h4", {}, b.text, remove);
  if (b.kind === "table") {
    return h("div", { class: "blk" }, h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, (b.head ?? []).map((x) => h("th", {}, x)))),
      h("tbody", {}, (b.rows ?? []).map((r) => h("tr", {}, r.map((c) => h("td", {}, c)))))), remove));
  }
  return h("div", { class: "blk" }, tag, evid, h("p", { class: "pre" }, withRefs(b.text)), remove);
}

function analystBlock(sec, b, editable) {
  if (!editable) {
    return h("div", { class: "blk" }, h("span", { class: `tag t-${b.kind}` }, KIND[b.kind] ?? b.kind), h("p", { class: "pre" }, b.text ? withRefs(`${b.text}${b.refs?.length ? " " + b.refs.map((n) => `[${n}]`).join("") : ""}`) : h("span", { class: "muted" }, "(vide)")));
  }
  const kind = h("select", { "aria-label": "Nature du paragraphe" }, ANALYST_KINDS.map((k) => h("option", { value: k, selected: k === b.kind }, KIND[k])));
  kind.addEventListener("change", () => { b.kind = kind.value; touch(); });
  const area = h("textarea", { rows: 4, maxlength: 20000, "aria-label": `${sec.title} : paragraphe`, placeholder: "Un fait cite ses sources (numéros ci-contre) ; sinon, qualifiez-le d'estimation ou d'hypothèse." });
  area.value = b.text;
  area.addEventListener("input", () => { b.text = area.value; touch(); });
  const refs = h("input", { type: "text", size: 10, value: (b.refs ?? []).join(", "), "aria-label": "Numéros de sources", placeholder: "ex. 1, 3", title: "Numéros de la table des sources, séparés par des virgules" });
  refs.addEventListener("input", () => { b.refs = [...new Set(refs.value.split(/[,\s]+/).map(Number).filter((n) => Number.isInteger(n) && n > 0))]; touch(); });
  return h("div", { class: "blk edit" }, h("div", { class: "blk-actions" }, kind, h("label", { class: "small muted" }, "Sources ", refs),
    h("button", { type: "button", class: "btn small-btn", onclick: () => { sec.blocks = sec.blocks.filter((x) => x.id !== b.id); touch(); rerender(); } }, "Supprimer")), area);
}

function sectionView(sec, idx, rec, editable) {
  const locked = sec.key === "sources";
  const moves = editable && !locked ? [
    h("button", { type: "button", class: "btn small-btn", "aria-label": "Monter la section", disabled: idx === 0, onclick: () => { const w = state.work; [w[idx - 1], w[idx]] = [w[idx], w[idx - 1]]; touch(); rerender(); } }, "↑"),
    h("button", { type: "button", class: "btn small-btn", "aria-label": "Descendre la section", disabled: idx >= state.work.length - 2, onclick: () => { const w = state.work; [w[idx + 1], w[idx]] = [w[idx], w[idx + 1]]; touch(); rerender(); } }, "↓"),
    h("button", { type: "button", class: "btn small-btn", onclick: () => { if (confirm(`Retirer la section « ${sec.title} » ?`)) { state.work.splice(idx, 1); touch(); rerender(); } } }, "Retirer la section"),
  ] : [];
  const na = sec.mode === "analyst" && editable
    ? h("label", { class: "small muted" }, h("input", { type: "checkbox", checked: sec.na, onchange: (e) => { sec.na = e.target.checked; touch(); } }), " Sans objet")
    : sec.na ? h("span", { class: "tag" }, "Sans objet") : null;
  const body = locked ? sourcesView(rec) : sec.blocks.map((b) => (b.auto ? autoBlock(sec, b, editable) : analystBlock(sec, b, editable)));
  const issues = state.issues.filter((i) => i.section === sec.key);
  return h("section", { class: "dl-sec", id: `sec-${sec.key}` },
    h("h3", {}, sec.title, h("span", { class: `tag ${sec.mode === "analyst" ? "t-todo" : ""}` }, sec.mode === "analyst" ? "à rédiger" : "calculé"), h("span", { class: "dl-tools" }, moves)),
    sec.mode === "analyst" && sec.guide ? h("p", { class: "muted small" }, sec.guide) : null,
    issues.map((i) => h("p", { class: i.severity === "error" ? "dl-warn" : "dl-note" }, i.message)),
    na, sec.na ? null : body,
    editable && !locked && !sec.na ? h("button", { type: "button", class: "btn small-btn", onclick: () => { sec.blocks.push({ id: `new-${++state.seq}`, kind: "estimate", text: "", refs: [], auto: false }); touch(); rerender(); } }, "+ Paragraphe") : null);
}

function sourcesView(rec) {
  return rec.sources.length ? h("div", { class: "table-wrap" }, h("table", {}, h("thead", {}, h("tr", {}, ["n", "Source", "Fiabilité", "Publié", "Titre", "Lien"].map((x) => h("th", {}, x)))),
    h("tbody", {}, rec.sources.map((s) => h("tr", { id: `src-${s.n}` }, h("td", {}, String(s.n)), h("td", {}, s.source_name, s.channel === "social" ? h("span", { class: "tag e-unconfirmed_social" }, "réseau social") : null),
      h("td", {}, s.reliability), h("td", {}, (s.published_at ?? "").slice(0, 10)), h("td", {}, s.title),
      h("td", {}, httpUrl(s.url) ? h("a", { class: s.channel === "social" ? "btn btn-social" : "btn", href: s.url, target: "_blank", rel: "noopener noreferrer",
        title: s.channel === "social" ? "Voir la publication (non vérifiée)" : "Lire l'article" }, s.channel === "social" ? "Post ↗" : "Lire ↗") : h("span", { class: "muted" }, "–"))))))) : h("p", { class: "muted" }, "Aucune source : le document ne contient aucun fait.");
}

async function download(fmt) {
  if (state.dirty) return say("Enregistrez vos modifications avant d'exporter : l'export porte sur la version enregistrée.");
  const out = await run(() => api.exportBlob(state.rec.id, fmt));
  if (!out) return;
  const url = URL.createObjectURL(out.blob);
  if (fmt === "html") window.open(url, "_blank", "noopener");
  else h("a", { href: url, download: out.name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
  say(fmt === "html" ? "Export HTML ouvert : Imprimer > Enregistrer au format PDF." : `Export ${fmt.toUpperCase()} téléchargé : ${out.name}`, true);
  openReport(state.rec.id, { keepIssues: true });
}

async function transition(action, needsComment) {
  if (state.dirty) return say("Enregistrez vos modifications d'abord.");
  let comment = "";
  if (needsComment) { comment = prompt(action === "reject" ? "Motif du rejet (obligatoire) :" : "Commentaire (facultatif) :") ?? null; if (comment === null) return; }
  const rec = await run(() => api.act(state.rec.id, action, { comment }));
  if (!rec) return;
  state.issues = [];
  say({ submit: "Envoyé en validation.", validate: "Document validé.", reject: "Renvoyé en brouillon.", archive: "Archivé." }[action] ?? "Fait.", true);
  await openReport(rec.id);
}

async function save() {
  const rec = state.rec;
  const meta = { classification: $("e-class").value, analyst: $("e-analyst").value.trim(), organization: $("e-org").value.trim(), recipient: $("e-recipient").value.trim() };
  const saved = await run(() => api.updateReport(rec.id, { expected_version: rec.version, title: $("e-title").value.trim(), meta, sections: state.work, note: "Modification" }));
  if (!saved) return;
  say(saved.version === rec.version ? "Aucun changement." : `Enregistré : version ${saved.version}.`, true);
  state.issues = [];
  await openReport(saved.id);
}

function sharePanel(rec) {
  const ok = rec.status === "validated" && SHAREABLE.has(rec.meta.classification);
  const why = rec.status !== "validated" ? "Partage possible seulement après validation." : !SHAREABLE.has(rec.meta.classification) ? `Diffusion par message bloquée : document ${rec.meta.classification}. Utilisez l'export contrôlé.` : "";
  const text = `${rec.title} — document validé (version ${rec.version}), confidentialité ${rec.meta.classification}.`;
  const btn = (label, onclick) => h("button", { type: "button", class: "btn", disabled: !ok, title: why || label, onclick }, label);
  return h("div", { class: "dl-share" }, h("b", { class: "small muted" }, "Partager"),
    btn("WhatsApp", () => window.open(`https://wa.me/?text=${encodeURIComponent(text)}`, "_blank", "noopener")),
    btn("E-mail", () => { location.href = `mailto:?subject=${encodeURIComponent(rec.title)}&body=${encodeURIComponent(text + "\n\n(Joindre l'export PDF téléchargé.)")}`; }),
    btn("Copier le lien", async () => { try { await navigator.clipboard.writeText(`${location.origin}${location.pathname}#${rec.id}`); say("Lien copié (valable sur ce poste uniquement).", true); } catch { say("Copie impossible : sélectionnez l'adresse de la page."); } }),
    h("span", { class: "muted small" }, ok ? "Le message ne contient que le titre et le statut, jamais le contenu. Les pièces jointes restent manuelles." : why));
}

async function loadVersions(box, rec) {
  clear(box);
  const versions = await run(() => api.versions(rec.id));
  if (!versions) return;
  const opts = (sel) => versions.map((v) => h("option", { value: v.version, selected: v.version === sel }, `v${v.version} · ${v.actor} · ${fmtDate(v.at)}${v.note ? ` · ${v.note}` : ""}`));
  const a = h("select", { "aria-label": "Version de départ" }, opts(Math.max(1, versions.length - 1)));
  const b = h("select", { "aria-label": "Version comparée" }, opts(versions.length));
  const out = h("div", {});
  box.append(h("div", { class: "blk-actions" }, a, "→", b, h("button", { type: "button", class: "btn", onclick: async () => {
    const changes = await run(() => api.diff(rec.id, Number(a.value), Number(b.value)));
    if (!changes) return;
    clear(out);
    if (!changes.length) out.append(h("p", { class: "muted" }, "Aucune différence."));
    for (const c of changes) out.append(h("div", { class: "diff" }, h("b", {}, `${c.title} (${{ added: "ajoutée", removed: "retirée", modified: "modifiée" }[c.change]})`),
      c.lines.map((ln) => h("div", { class: ln.startsWith("+") ? "add" : ln.startsWith("-") ? "del" : "" }, ln))));
  } }, "Comparer")), out);
}

function renderEditor() {
  const rec = state.rec, el = $("editor");
  clear(el);
  if (!rec) { el.append(h("p", { class: "muted" }, "Sélectionnez un livrable ou générez-en un.")); return; }
  const editable = canEdit();
  const m = rec.meta;
  const field = (id, label, value, max = 200) => h("label", {}, label, h("input", { id, value, maxlength: max, disabled: !editable, oninput: touch }));
  const cls = h("select", { id: "e-class", disabled: !editable, onchange: touch }, ["PUBLIC", "INTERNE", "CONFIDENTIEL", "RESTREINT"].map((c) => h("option", { value: c, selected: c === m.classification }, c)));

  const actions = [];
  if (editable) actions.push(h("button", { type: "button", class: "btn go", id: "save-btn", disabled: !state.dirty, onclick: save }, "Enregistrer"));
  actions.push(h("button", { type: "button", class: "btn", onclick: () => run(async () => { const r = await api.check(rec.id); state.issues = r.issues; renderEditor(); say(r.issues.some((i) => i.severity === "error") ? "Points bloquants avant validation (voir dans les sections)." : "Aucun point bloquant.", !r.issues.some((i) => i.severity === "error")); }) }, "Contrôler"));
  if (rec.status === "draft" && EDIT.has(state.role)) actions.push(h("button", { type: "button", class: "btn go", onclick: () => transition("submit", false) }, "Soumettre à validation"));
  if (rec.status === "in_review" && VALIDATE.has(state.role)) actions.push(
    h("button", { type: "button", class: "btn go", onclick: () => transition("validate", true) }, "Valider"),
    h("button", { type: "button", class: "btn", onclick: () => transition("reject", true) }, "Rejeter"));
  if (EDIT.has(state.role)) actions.push(h("button", { type: "button", class: "btn", onclick: async () => { const c = await run(() => api.act(rec.id, "duplicate")); if (c) { await refreshList(); openReport(c.id); } } }, "Dupliquer"));
  if ((EDIT.has(state.role) || VALIDATE.has(state.role)) && ["draft", "validated"].includes(rec.status)) actions.push(h("button", { type: "button", class: "btn", onclick: () => confirm("Archiver ce document ?") && transition("archive", false) }, "Archiver"));
  const wm = rec.status === "validated" || rec.status === "archived" ? "" : " Brouillon : le fichier porte le filigrane NON VALIDÉ.";
  const exports = [
    h("button", { type: "button", class: "btn", disabled: state.caps && !state.caps.pdf, title: state.caps?.pdf ? `Télécharger le PDF.${wm}` : "PDF indisponible : installer reportlab (pip install reportlab). Utilisez l'export HTML puis Imprimer.", onclick: () => download("pdf") }, "Télécharger PDF"),
    h("button", { type: "button", class: "btn", title: `Document Word.${wm}`, onclick: () => download("docx") }, "Word (.docx)"),
    h("button", { type: "button", class: "btn", title: `Ouvre la page A4 ; Imprimer > Enregistrer au format PDF.${wm}`, onclick: () => download("html") }, "Aperçu / impression"),
  ];

  el.append(...[ // Element.append ne déplie pas les tableaux et écrirait « null » : on aplatit et on filtre
    h("div", { class: "dl-meta" }, h("span", { class: `badge st-${rec.status}` }, STATUS[rec.status]), h("span", { class: "muted" }, `version ${rec.version} · créé par ${rec.created_by} · modifié par ${rec.last_editor} · ${fmtDate(rec.updated_at)}`),
      rec.validated_by ? h("span", { class: "muted" }, `· validé par ${rec.validated_by}, ${fmtDate(rec.validated_at)}`) : null, h("span", { class: "tag t-todo", id: "dirty", hidden: !state.dirty }, "modifications non enregistrées")),
    h("label", { class: "dl-title" }, "Titre ", h("input", { id: "e-title", value: rec.title, maxlength: 300, disabled: !editable, oninput: touch })),
    m.demo ? h("p", { class: "dl-warn" }, "Données fictives de démonstration : filigrane maintenu sur tous les exports.") : null,
    rec.warnings.map((w) => h("p", { class: "dl-note" }, w)),
    rec.status !== "draft" ? h("p", { class: "dl-note" }, "Document verrouillé : pour le modifier, le rejeter (validateur) ou le dupliquer.") : null,
    h("details", {}, h("summary", {}, "Informations du document"), h("div", { class: "dl-info" }, field("e-analyst", "Analyste ", m.analyst), field("e-org", "Organisation ", m.organization), field("e-recipient", "Destinataire ", m.recipient), h("label", {}, "Confidentialité ", cls))),
    h("div", { class: "dl-actions" }, actions),
    h("div", { class: "dl-actions" }, h("b", { class: "small muted" }, "Exporter"), exports),
    sharePanel(rec),
    h("p", { class: "muted small" }, state.caps?.llm ? "" : "Assistant IA non connecté : aucune clé configurée. Correction, résumé, développement et traduction restent manuels."),
    state.issues.length && !state.issues.some((i) => i.section) ? h("ul", { class: "dl-warn" }, state.issues.map((i) => h("li", {}, i.message))) : null,
    state.work.map((s, i) => sectionView(s, i, rec, editable)),
    h("details", {}, h("summary", {}, `Versions (${rec.versions.length}) et comparaison`), h("div", { id: "versions", class: "dl-versions" })),
    h("details", {}, h("summary", {}, `Journal d'audit (${rec.audit.length})`), h("ul", { class: "small" }, rec.audit.map((a) => h("li", {}, `${fmtDate(a.at)} · ${a.actor} (${a.role}) · ${a.action}${a.detail ? ` · ${a.detail}` : ""}`)))),
  ].flat().filter(Boolean));
  const vbox = $("versions");
  vbox.parentElement.addEventListener("toggle", (e) => { if (e.target.open) loadVersions(vbox, rec); }, { once: true });
}

// --- démarrage ------------------------------------------------------------------------------------------
async function main() {
  initIdentity();
  try {
    const [meta, risk, caps, catalog] = await Promise.all([api.meta(), api.risk(), api.capabilities(), api.catalog("fr")]);
    Object.assign(state, { meta, caps, catalog, byId: Object.fromEntries(catalog.map((t) => [t.id, t])), countries: [...risk].sort((a, b) => a.country_name.localeCompare(b.country_name, "fr")) });
  } catch (err) {
    $("editor").replaceChildren(h("p", {}, `Impossible de charger les livrables : ${err.message}`));
    return;
  }
  const banner = $("demo-banner");
  banner.hidden = false;
  banner.textContent = state.meta.demo ? "Données fictives de démonstration" : "Sources réelles : CGU à valider avant toute exploitation durable";
  renderForm();
  applyQuery();
  $("l-status").addEventListener("change", () => run(refreshList));
  $("l-q").addEventListener("input", () => run(refreshList));
  window.addEventListener("beforeunload", (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ""; } });
  window.addEventListener("hashchange", () => { const id = location.hash.slice(1); if (id && id !== state.rec?.id) openReport(id); });
  await run(refreshList);
  const id = location.hash.slice(1);
  if (id) await openReport(id);
}
main();
