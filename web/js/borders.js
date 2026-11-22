// Frontières : décodage TopoJSON (world-atlas, Natural Earth, domaine public) sans bibliothèque,
// correspondance code ISO numérique -> ISO3 et tracé SVG avec élagage et simplification à l'écran.

import { fromScreen } from "./mercator.js";

// Version figée (world-atlas, données Natural Earth 1:50 000 000). Chargée par le navigateur.
export const BORDERS_URL = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-50m.json";
export const BORDERS_ATTRIBUTION = "Frontières : Natural Earth (domaine public), tracé indicatif sans valeur juridique";

// Codes ISO 3166-1 numériques des 54 pays africains (+ Sahara occidental).
export const NUM_TO_ISO3 = {
  "012": "DZA", "024": "AGO", "204": "BEN", "072": "BWA", "854": "BFA", "108": "BDI", "132": "CPV", "120": "CMR",
  "140": "CAF", "148": "TCD", "174": "COM", "178": "COG", "180": "COD", "384": "CIV", "262": "DJI", "818": "EGY",
  "226": "GNQ", "232": "ERI", "748": "SWZ", "231": "ETH", "266": "GAB", "270": "GMB", "288": "GHA", "324": "GIN",
  "624": "GNB", "404": "KEN", "426": "LSO", "430": "LBR", "434": "LBY", "450": "MDG", "454": "MWI", "466": "MLI",
  "478": "MRT", "480": "MUS", "504": "MAR", "508": "MOZ", "516": "NAM", "562": "NER", "566": "NGA", "646": "RWA",
  "678": "STP", "686": "SEN", "690": "SYC", "694": "SLE", "706": "SOM", "710": "ZAF", "728": "SSD", "729": "SDN",
  "834": "TZA", "768": "TGO", "788": "TUN", "800": "UGA", "894": "ZMB", "716": "ZWE", "732": "ESH",
};
// Entités sans code numérique dans les données : rattachées au pays qui les revendique.
const BY_NAME = { Somaliland: "SOM" };

export function featureIso(feature) {
  return NUM_TO_ISO3[feature.id] ?? BY_NAME[feature.name] ?? null;
}

/** Décode une topologie TopoJSON quantifiée ou non. @returns {{id:string|undefined,name:string,iso3:string|null,rings:{pts:number[][],bbox:number[]}[]}[]} */
export function decodeTopology(topo, objectName = "countries") {
  const t = topo.transform;
  const arcs = topo.arcs.map((arc) => {
    let x = 0, y = 0;
    return arc.map(([dx, dy]) => {
      if (!t) return [dx, dy];
      x += dx; y += dy;
      return [x * t.scale[0] + t.translate[0], y * t.scale[1] + t.translate[1]];
    });
  });
  const ring = (indices) => {
    const pts = [];
    for (const i of indices) {
      const a = i >= 0 ? arcs[i] : [...arcs[~i]].reverse();
      pts.push(...(pts.length ? a.slice(1) : a));
    }
    let [x0, y0, x1, y1] = [Infinity, Infinity, -Infinity, -Infinity];
    for (const [x, y] of pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    return { pts, bbox: [x0, y0, x1, y1] };
  };
  const out = [];
  for (const g of topo.objects[objectName].geometries) {
    let rings = [];
    if (g.type === "Polygon") rings = g.arcs.map(ring);
    else if (g.type === "MultiPolygon") rings = g.arcs.flatMap((poly) => poly.map(ring));
    else continue;
    const feature = { id: g.id === undefined ? undefined : String(g.id).padStart(3, "0"), name: g.properties?.name ?? "", rings };
    out.push({ ...feature, iso3: featureIso(feature) });
  }
  return out;
}

export async function loadBorders(url = BORDERS_URL) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return decodeTopology(await res.json());
}

export const BORDERS_URL_COARSE = BORDERS_URL.replace("countries-50m", "countries-110m");

/**
 * Chargement progressif : les deux jeux sont demandés en parallèle ; le plus léger (110 m) s'affiche dès qu'il
 * arrive, le détaillé (50 m) le remplace ensuite. Un échec d'un jeu n'empêche pas l'autre.
 * @param {(features: ReturnType<typeof decodeTopology>) => void} onData appelé à chaque jeu reçu
 * @returns {Promise<"fine"|"coarse"|null>} meilleur niveau obtenu (null : aucun)
 */
export async function loadBordersProgressive(onData, { coarse = BORDERS_URL_COARSE, fine = BORDERS_URL } = {}) {
  let level = null;
  await Promise.all([
    loadBorders(coarse).then((b) => { if (level !== "fine") { level = "coarse"; onData(b); } }).catch(() => {}),
    loadBorders(fine).then((b) => { level = "fine"; onData(b); }).catch(() => {}),
  ]);
  return level;
}

/** Bornes géographiques visibles : [lonMin, latMin, lonMax, latMax]. */
export function viewBounds(view) {
  const [lon0, lat0] = fromScreen(0, 0, view), [lon1, lat1] = fromScreen(view.w, view.h, view);
  return [Math.min(lon0, lon1), Math.min(lat0, lat1), Math.max(lon0, lon1), Math.max(lat0, lat1)];
}

const intersects = (a, b) => a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];

/**
 * Chemin SVG d'un pays pour la vue courante. Les anneaux hors écran sont ignorés ; les sommets
 * distants de moins de `minStep` pixels du précédent sont omis (le coût suit l'écran, pas le détail des données).
 * @param {(lon:number,lat:number)=>[number,number]} project
 */
export function featurePath(feature, bounds, project, minStep = 0.9) {
  let d = "";
  for (const r of feature.rings) {
    if (!intersects(r.bbox, bounds)) continue;
    let px = 0, py = 0, n = 0;
    const last = r.pts.length - 1;
    for (let i = 0; i <= last; i++) {
      const [x, y] = project(r.pts[i][0], r.pts[i][1]);
      if (n > 0 && i !== last && Math.abs(x - px) + Math.abs(y - py) < minStep) continue;
      d += `${n === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
      px = x; py = y; n += 1;
    }
    if (n > 2) d += "Z";
    else d = d.slice(0, d.length - (n ? 1 : 0)) && d; // anneau dégénéré : conservé tel quel, sans effet visible
  }
  return d;
}
