// Carte navigable à deux fonds : « Satellite » (tuiles NASA GIBS) et « Map » (fond uni). Dans les deux cas,
// les pays sont dessinés avec leurs frontières et colorés selon le niveau de risque.
// Navigation : molette, boutons, double-clic, glisser, pincement. Villes selon le niveau de zoom.
// Repli : si les frontières ne chargent pas, ou pour un pays sans polygone (petites îles), un point est dessiné.

import { CENTROIDS } from "./centroids.js";
import { CITIES } from "./cities.js";
import { clear, svgEl } from "./dom.js";
import { featurePath, viewBounds } from "./borders.js";
import { ATTRIBUTION, fitAfrica, panBy, tileUrl, toScreen, visibleTiles, zoomAt, ZOOM_MAX } from "./mercator.js";

export const W = 800, H = 650;
const CAPITAL_ZOOM = 4.2, CITY_ZOOM = 5.4;
const NAME_ZOOM = { sat: 4.8, map: 3.8 };

export function createMap(svg, { onSelect, onNote, tip, basemap = "sat" }) {
  let view = fitAfrica(W, H);
  let items = [];
  let borders = null; // Map iso3 -> entités (anneaux) issues de borders.js
  let base = basemap;
  let raf = 0, loaded = 0, failed = 0;
  const tileEls = new Map();
  const pointers = new Map();
  let drag = null, pinch = null, suppressClick = false;

  clear(svg);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const gSea = svgEl("rect", { x: 0, y: 0, width: W, height: H, fill: "#0a1020" });
  const gBase = svgEl("g"), gFine = svgEl("g"), gShade = svgEl("g"), gPoly = svgEl("g"), gCities = svgEl("g"), gDots = svgEl("g"), gNames = svgEl("g");
  gShade.append(svgEl("rect", { x: 0, y: 0, width: W, height: H, fill: "#000", "fill-opacity": "0.2", "pointer-events": "none" }));
  svg.append(gSea, gBase, gFine, gShade, gPoly, gCities, gDots, gNames);

  function syncTiles(group, tiles, prefix) {
    const keep = new Set();
    for (const t of tiles) {
      const key = `${prefix}:${t.key}`;
      keep.add(key);
      let el = tileEls.get(key);
      if (!el) {
        el = svgEl("image", { href: tileUrl(t.z, t.x, t.y), width: 0, height: 0 });
        el.addEventListener("load", () => { loaded += 1; });
        el.addEventListener("error", () => {
          el.setAttribute("visibility", "hidden");
          failed += 1;
          if (loaded === 0 && failed >= 4) onNote?.("Fond satellite indisponible (réseau ?). Passez en mode Map.");
        });
        tileEls.set(key, el);
        group.append(el);
      }
      el.setAttribute("x", (t.sx - 0.25).toFixed(1));
      el.setAttribute("y", (t.sy - 0.25).toFixed(1));
      el.setAttribute("width", (t.size + 0.5).toFixed(1));
      el.setAttribute("height", (t.size + 0.5).toFixed(1));
    }
    for (const [key, el] of tileEls) if (key.startsWith(prefix + ":") && !keep.has(key)) { el.remove(); tileEls.delete(key); }
  }

  const showTip = (e, text) => { tip.hidden = false; tip.style.left = e.clientX + 12 + "px"; tip.style.top = e.clientY + 12 + "px"; tip.textContent = text; };

  // Éléments persistants : on ne crée un nœud (et ses écouteurs) qu'une fois ; ensuite seuls les attributs changent.
  const polyEls = new Map(), cityEls = [], nameEls = new Map();
  let itemByIso = new Map(), geomDirty = true;

  const setAttr = (el, name, value) => { if (el.getAttribute(name) !== value) el.setAttribute(name, value); }; // évite les écritures sans effet
  const show = (el, on) => { const v = on ? "" : "none"; if (el.style.display !== v) el.style.display = v; };

  function polyEl(iso) {
    let path = polyEls.get(iso);
    if (path) return path;
    path = svgEl("path", { "fill-rule": "evenodd" });
    const pick = () => { if (!suppressClick && itemByIso.has(iso)) onSelect(iso); };
    path.addEventListener("click", pick);
    path.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    path.addEventListener("mousemove", (e) => { const it = itemByIso.get(iso); if (it) showTip(e, it.tip); });
    path.addEventListener("mouseleave", () => { tip.hidden = true; });
    polyEls.set(iso, path);
    gPoly.append(path);
    return path;
  }

  function restyle() { // couleurs, sélection, accessibilité : sans recalculer les tracés
    for (const [iso, path] of polyEls) {
      const it = itemByIso.get(iso);
      setAttr(path, "class", `poly lv-${it ? it.level : "unrated"}${it?.selected ? " sel" : ""}${it && !it.visible ? " dim" : ""}`);
      if (it) {
        setAttr(path, "role", "button"); setAttr(path, "tabindex", "0"); setAttr(path, "aria-label", it.aria);
        if (it.selected && gPoly.lastChild !== path) gPoly.append(path); // au premier plan : le contour du pays choisi n'est pas masqué par ses voisins
      } else {
        path.removeAttribute("role"); path.removeAttribute("tabindex"); path.removeAttribute("aria-label");
      }
    }
  }

  function drawGeometry() {
    const bounds = viewBounds(view);
    const project = (lon, lat) => toScreen(lon, lat, view);
    for (const [iso, features] of borders) {
      const path = polyEl(iso);
      const d = features.map((f) => featurePath(f, bounds, project)).join("");
      if (d) { setAttr(path, "d", d); show(path, true); } else show(path, false);
    }
    geomDirty = false;
  }

  function drawCities() {
    if (!cityEls.length) {
      for (const c of CITIES) {
        const dot = svgEl("circle", { r: c.capital ? 3.5 : 2.5, class: c.capital ? "city cap" : "city" });
        const lbl = svgEl("text", { class: "citylbl" }, c.name);
        gCities.append(dot, lbl);
        cityEls.push({ c, dot, lbl });
      }
    }
    for (const { c, dot, lbl } of cityEls) {
      let on = view.zoom >= CAPITAL_ZOOM && (c.capital || view.zoom >= CITY_ZOOM);
      let x = 0, y = 0;
      if (on) { [x, y] = toScreen(c.lon, c.lat, view); on = x >= -20 && x <= W + 20 && y >= -20 && y <= H + 20; }
      show(dot, on); show(lbl, on);
      if (!on) continue;
      setAttr(dot, "cx", x.toFixed(1)); setAttr(dot, "cy", y.toFixed(1));
      setAttr(lbl, "x", (x + 6).toFixed(1)); setAttr(lbl, "y", (y + 3.5).toFixed(1));
    }
  }

  function drawMarkers() { // points de repli (pays sans polygone) et noms de pays
    clear(gDots);
    const noPolygon = (iso) => !(borders && borders.has(iso));
    for (const it of items) {
      const [x, y] = toScreen(...CENTROIDS[it.iso3], view);
      const inView = x >= -20 && x <= W + 20 && y >= -20 && y <= H + 20;
      if (inView && noPolygon(it.iso3)) {
        gDots.append(svgEl("circle", {
          cx: x.toFixed(1), cy: y.toFixed(1), r: view.zoom >= 5 ? 8 : 10, tabindex: 0, role: "button",
          class: `dot lv-${it.level}${it.selected ? " sel" : ""}${it.visible ? "" : " dim"}`, "aria-label": it.aria,
          onclick: () => { if (!suppressClick) onSelect(it.iso3); },
          onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onSelect(it.iso3); } },
          onmousemove: (e) => showTip(e, it.tip),
          onmouseleave: () => { tip.hidden = true; },
        }));
      }
      let label = nameEls.get(it.iso3);
      const wanted = inView && (it.selected || view.zoom >= NAME_ZOOM[base]);
      if (!label && wanted) { label = svgEl("text", { class: "slbl" }, it.name); nameEls.set(it.iso3, label); gNames.append(label); }
      if (!label) continue;
      show(label, wanted);
      if (wanted) { setAttr(label, "x", x.toFixed(1)); setAttr(label, "y", y.toFixed(1)); }
    }
  }

  function draw() {
    raf = 0;
    const sat = base === "sat";
    svg.classList.toggle("sat", sat);
    svg.classList.toggle("flat", !sat);
    gBase.style.display = gFine.style.display = gShade.style.display = sat ? "" : "none";
    if (sat) {
      const level = Math.round(view.zoom);
      syncTiles(gBase, visibleTiles(view, Math.max(2, level - 3)), "b");
      syncTiles(gFine, visibleTiles(view, level), "f");
    }
    if (borders && geomDirty) drawGeometry();
    drawCities();
    drawMarkers();
  }

  const schedule = () => { if (!raf) raf = requestAnimationFrame(draw); };
  const toLocal = (e) => {
    const r = svg.getBoundingClientRect();
    return [((e.clientX - r.left) * W) / r.width, ((e.clientY - r.top) * H) / r.height];
  };
  const apply = (next) => { if (next !== view) { view = next; geomDirty = true; schedule(); } };

  const onWheel = (e) => {
    e.preventDefault();
    const [sx, sy] = toLocal(e);
    apply(zoomAt(view, 2 ** (-Math.sign(e.deltaY) * Math.min(Math.abs(e.deltaY), 100) * 0.0035), sx, sy));
  };
  const onDown = (e) => {
    pointers.set(e.pointerId, toLocal(e));
    if (pointers.size === 1) drag = { x: e.clientX, y: e.clientY, moved: false };
    if (pointers.size === 2) { drag = null; pinch = null; }
  };
  const onMove = (e) => {
    if (!pointers.has(e.pointerId)) return;
    const prev = pointers.get(e.pointerId), cur = toLocal(e);
    pointers.set(e.pointerId, cur);
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const dist = Math.hypot(a[0] - b[0], a[1] - b[1]), mid = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
      if (pinch) apply(zoomAt(view, dist / pinch.dist, mid[0], mid[1]));
      pinch = { dist };
      suppressClick = true;
      return;
    }
    if (drag) {
      if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < 5) return;
      drag.moved = true;
      suppressClick = true;
      svg.classList.add("grabbing");
      tip.hidden = true;
      apply(panBy(view, cur[0] - prev[0], cur[1] - prev[1]));
    }
  };
  const onUp = (e) => {
    pointers.delete(e.pointerId);
    if (pointers.size < 2) pinch = null;
    if (pointers.size === 0) { drag = null; svg.classList.remove("grabbing"); setTimeout(() => { suppressClick = false; }, 0); }
  };
  const onDbl = (e) => { const [sx, sy] = toLocal(e); apply(zoomAt(view, 2, sx, sy)); };

  svg.addEventListener("wheel", onWheel, { passive: false });
  svg.addEventListener("pointerdown", onDown);
  window.addEventListener("pointermove", onMove);
  window.addEventListener("pointerup", onUp);
  window.addEventListener("pointercancel", onUp);
  svg.addEventListener("dblclick", onDbl);

  return {
    update(next) { // données ou sélection modifiées : on ne recalcule pas les tracés, seulement styles et marqueurs
      items = next; itemByIso = new Map(next.map((it) => [it.iso3, it]));
      if (borders) { for (const iso of borders.keys()) polyEl(iso); restyle(); }
      schedule();
    },
    setBorders(features) { // regroupe par pays ; les entités non africaines (iso3 nul) sont ignorées
      const byIso = new Map();
      for (const f of features) if (f.iso3) byIso.set(f.iso3, [...(byIso.get(f.iso3) ?? []), f]);
      borders = byIso; geomDirty = true;
      for (const [iso, path] of polyEls) if (!byIso.has(iso)) { path.remove(); polyEls.delete(iso); } // jeu de frontières remplacé
      for (const iso of byIso.keys()) polyEl(iso);
      restyle();
      schedule();
    },
    setBasemap(b) { if (b !== base) { base = b; schedule(); } },
    zoomBy(factor) { apply(zoomAt(view, factor, W / 2, H / 2)); },
    reset() { apply(fitAfrica(W, H)); },
    focus(iso3, minZoom = 5) {
      const c = CENTROIDS[iso3];
      if (c) apply({ ...view, lon: c[0], lat: c[1], zoom: Math.min(ZOOM_MAX, Math.max(view.zoom, minZoom)) });
    },
    get zoom() { return view.zoom; },
    get hasBorders() { return borders !== null; },
    attribution: ATTRIBUTION,
    destroy() {
      cancelAnimationFrame(raf);
      svg.removeEventListener("wheel", onWheel);
      svg.removeEventListener("pointerdown", onDown);
      svg.removeEventListener("dblclick", onDbl);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onUp);
      svg.classList.remove("grabbing", "sat", "flat");
      clear(svg);
    },
  };
}
