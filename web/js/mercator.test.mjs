import test from "node:test";
import assert from "node:assert/strict";
import { CENTROIDS } from "./centroids.js";
import { satelliteGrid, tileUrl, worldX, worldY } from "./mercator.js";

test("projection : équateur au milieu, méridien de Greenwich à mi-monde", () => {
  assert.ok(Math.abs(worldY(0) - 0.5) < 1e-9);
  assert.ok(Math.abs(worldX(0) - 0.5) < 1e-9);
  assert.ok(worldY(40) < worldY(0) && worldY(-40) > worldY(0)); // le nord est en haut
});

test("la grille couvre tous les pays, dans le cadre", () => {
  const g = satelliteGrid(4);
  assert.equal(g.tiles.length * 1, (g.width / 256) * (g.height / 256));
  for (const [iso, [lon, lat]] of Object.entries(CENTROIDS)) {
    const [x, y] = g.project(lon, lat);
    assert.ok(x > 0 && x < g.width && y > 0 && y < g.height, `${iso} hors cadre : ${x},${y}`);
  }
});

test("géographie : Le Caire au nord-est de Dakar, Le Cap au sud", () => {
  const g = satelliteGrid(4);
  const dakar = g.project(-17.4, 14.7), caire = g.project(31.2, 30.0), cap = g.project(18.4, -33.9);
  assert.ok(caire[0] > dakar[0] && caire[1] < dakar[1]);
  assert.ok(cap[1] > dakar[1]);
});

test("URL de tuile : ordre niveau/ligne/colonne", () => {
  assert.match(tileUrl(4, 8, 7), /GoogleMapsCompatible_Level8\/4\/7\/8\.jpeg$/);
});

test("nombre de tuiles raisonnable au niveau 4", () => {
  assert.ok(satelliteGrid(4).tiles.length <= 30);
});

import { clampView, fitAfrica, fromScreen, panBy, toScreen, visibleTiles, zoomAt, ZOOM_MAX, ZOOM_MIN } from "./mercator.js";
import { CITIES } from "./cities.js";

test("toScreen et fromScreen sont inverses", () => {
  const v = { lon: 10, lat: 5, zoom: 5, w: 800, h: 600 };
  const [x, y] = toScreen(20, -10, v);
  const [lon, lat] = fromScreen(x, y, v);
  assert.ok(Math.abs(lon - 20) < 1e-6 && Math.abs(lat + 10) < 1e-6);
  const [cx, cy] = toScreen(10, 5, v);
  assert.ok(Math.abs(cx - 400) < 1e-6 && Math.abs(cy - 300) < 1e-6);
});

test("le zoom garde fixe le point sous le curseur", () => {
  const v = fitAfrica(800, 650);
  const sx = 520, sy = 240;
  const before = fromScreen(sx, sy, v);
  const next = zoomAt(v, 2, sx, sy);
  assert.ok(next.zoom > v.zoom);
  const [ax, ay] = toScreen(before[0], before[1], next);
  assert.ok(Math.abs(ax - sx) < 0.5 && Math.abs(ay - sy) < 0.5, `${ax},${ay}`);
});

test("zoom borné", () => {
  let v = fitAfrica(800, 650);
  for (let i = 0; i < 30; i++) v = zoomAt(v, 2, 400, 300);
  assert.equal(v.zoom, ZOOM_MAX);
  for (let i = 0; i < 30; i++) v = zoomAt(v, 0.5, 400, 300);
  assert.equal(v.zoom, ZOOM_MIN);
});

test("déplacement : tirer vers la droite montre l'ouest, et reste borné", () => {
  const v = { lon: 10, lat: 5, zoom: 5, w: 800, h: 600 };
  assert.ok(panBy(v, 100, 0).lon < v.lon);
  assert.ok(panBy(v, 0, 100).lat > v.lat);
  assert.ok(clampView(panBy(v, -1e7, 0)).lon <= 61);
});

test("fitAfrica cadre tous les pays à l'écran", () => {
  const v = fitAfrica(800, 650);
  for (const [iso, [lon, lat]] of Object.entries(CENTROIDS)) {
    const [x, y] = toScreen(lon, lat, v);
    assert.ok(x > 0 && x < 800 && y > 0 && y < 650, `${iso} hors écran`);
  }
});

test("tuiles visibles : couvrent l'écran, nombre raisonnable, indices valides", () => {
  for (const zoom of [3, 4.4, 6, 8]) {
    const v = { lon: 10, lat: 5, zoom, w: 800, h: 650 };
    const tiles = visibleTiles(v);
    assert.ok(tiles.length > 0 && tiles.length < 60, `${zoom}: ${tiles.length}`);
    const n = 2 ** tiles[0].z;
    for (const t of tiles) assert.ok(t.x >= 0 && t.x < n && t.y >= 0 && t.y < n);
    assert.ok(Math.min(...tiles.map((t) => t.sx)) <= 0 && Math.max(...tiles.map((t) => t.sx + t.size)) >= 800);
    assert.ok(Math.min(...tiles.map((t) => t.sy)) <= 0 && Math.max(...tiles.map((t) => t.sy + t.size)) >= 650);
  }
});

test("villes : toutes rattachées à un pays connu, dans l'emprise, une capitale par pays", () => {
  const caps = new Set();
  for (const c of CITIES) {
    assert.ok(c.iso3 in CENTROIDS, c.name);
    assert.ok(c.lon > -27 && c.lon < 61 && c.lat > -38 && c.lat < 39, c.name);
    if (c.capital) caps.add(c.iso3);
  }
  assert.ok(caps.size >= 53, `capitales : ${caps.size}`);
});
