import test from "node:test";
import assert from "node:assert/strict";
import { CENTROIDS } from "./centroids.js";
import { decodeTopology, featureIso, featurePath, loadBordersProgressive, NUM_TO_ISO3, viewBounds } from "./borders.js";
import { fitAfrica, toScreen } from "./mercator.js";

// Deux carrés qui partagent un côté (arc 1), l'un parcouru à l'envers. Quantifié avec delta-encodage.
const topo = {
  type: "Topology",
  transform: { scale: [1, 1], translate: [0, 0] },
  arcs: [
    [[0, 0], [10, 0], [0, 10], [-10, 0]],   // arc 0 : (0,0) (10,0) (10,10) (0,10)
    [[0, 10], [0, -10]],                    // arc 1 : (0,10) (0,0)  — côté commun
    [[10, 0], [10, 0], [0, 10], [-10, 0]],  // arc 2 : (10,0) (20,0) (20,10) (10,10)
    [[10, 10], [0, -10]],                   // arc 3 : (10,10) (10,0) — côté commun
  ],
  objects: { countries: { type: "GeometryCollection", geometries: [
    { type: "Polygon", id: "466", properties: { name: "Mali" }, arcs: [[0, 1]] },
    { type: "Polygon", id: "562", properties: { name: "Niger" }, arcs: [[2, 3]] },
    { type: "MultiPolygon", properties: { name: "Somaliland" }, arcs: [[[~0]]] },
  ] } },
};

test("décodage : sommets, anneaux fermés et bbox", () => {
  const [a, b] = decodeTopology(topo);
  assert.equal(a.iso3, "MLI");
  assert.deepEqual(a.rings[0].bbox, [0, 0, 10, 10]);
  assert.deepEqual(b.rings[0].bbox, [10, 0, 20, 10]);
  assert.ok(a.rings[0].pts.length >= 4);
});

test("arcs inversés (indice négatif) et entités sans code", () => {
  const f = decodeTopology(topo)[2];
  assert.equal(f.id, undefined);
  assert.equal(f.iso3, "SOM"); // Somaliland rattaché à la Somalie
  assert.deepEqual(f.rings[0].pts[0], [0, 10]); // arc 0 parcouru à l'envers
});

test("correspondance ISO : 54 pays + Sahara occidental, identique aux centroïdes, sans doublon", () => {
  const isos = Object.values(NUM_TO_ISO3);
  assert.equal(new Set(isos).size, isos.length);
  assert.equal(isos.length, 55);
  for (const iso of Object.keys(CENTROIDS)) assert.ok(isos.includes(iso), iso);
  assert.equal(featureIso({ id: "004", name: "Afghanistan" }), null);
});

test("tracé : élagage des anneaux hors écran et simplification", () => {
  const view = fitAfrica(800, 650);
  const project = (lon, lat) => toScreen(lon, lat, view);
  const [mali] = decodeTopology(topo);
  assert.ok(featurePath(mali, viewBounds(view), project).startsWith("M"));
  const far = { rings: [{ pts: [[100, 60], [101, 60], [101, 61]], bbox: [100, 60, 101, 61] }] };
  assert.equal(featurePath(far, viewBounds(view), project), "");
  const dense = { rings: [{ pts: Array.from({ length: 1000 }, (_, i) => [10 + i * 1e-5, 5]).concat([[10, 5]]), bbox: [10, 5, 10.01, 5] }] };
  const d = featurePath(dense, viewBounds(view), project);
  assert.ok((d.match(/[ML]/g) ?? []).length < 20, "simplification insuffisante");
});

function stubFetch(plan) { // plan : { url: délai en ms | "fail" }
  const real = globalThis.fetch;
  globalThis.fetch = (url) => new Promise((resolve, reject) => {
    const how = plan[url];
    if (how === "fail") return reject(new Error("réseau"));
    setTimeout(() => resolve({ ok: true, json: async () => topo }), how);
  });
  return () => { globalThis.fetch = real; };
}

test("progressif : le jeu léger s'affiche d'abord, le détaillé le remplace", async () => {
  const restore = stubFetch({ c: 5, f: 40 });
  try {
    const seen = [];
    const level = await loadBordersProgressive((b) => seen.push(b.length), { coarse: "c", fine: "f" });
    assert.equal(level, "fine");
    assert.equal(seen.length, 2);
  } finally { restore(); }
});

test("progressif : le jeu léger arrivé après le détaillé est ignoré", async () => {
  const restore = stubFetch({ c: 40, f: 5 });
  try {
    let calls = 0;
    assert.equal(await loadBordersProgressive(() => { calls += 1; }, { coarse: "c", fine: "f" }), "fine");
    assert.equal(calls, 1);
  } finally { restore(); }
});

test("progressif : repli sur le jeu léger si le détaillé échoue, null si tout échoue", async () => {
  let restore = stubFetch({ c: 1, f: "fail" });
  try { assert.equal(await loadBordersProgressive(() => {}, { coarse: "c", fine: "f" }), "coarse"); } finally { restore(); }
  restore = stubFetch({ c: "fail", f: "fail" });
  try { assert.equal(await loadBordersProgressive(() => {}, { coarse: "c", fine: "f" }), null); } finally { restore(); }
});
