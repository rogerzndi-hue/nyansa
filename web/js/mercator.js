// Fond satellite : projection Web Mercator et grille de tuiles (aucune bibliothèque cartographique).

export const BBOX = { lonMin: -27, lonMax: 61, latMin: -38, latMax: 39 }; // Afrique, îles comprises
export const TILE = 256;

// NASA GIBS « Blue Marble » : données ouvertes de la NASA, sans clé. Niveaux 0 à 8.
export const tileUrl = (z, x, y) =>
  `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_ShadedRelief_Bathymetry/default/GoogleMapsCompatible_Level8/${z}/${y}/${x}.jpeg`;
export const ATTRIBUTION = "Imagerie : NASA EOSDIS GIBS (Blue Marble)";

/** Coordonnées normalisées 0..1 du monde en Web Mercator. */
export const worldX = (lon) => (lon + 180) / 360;
export const worldY = (lat) => {
  const r = (lat * Math.PI) / 180;
  return (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2;
};

/** @returns {{width:number,height:number,tiles:{x:number,y:number,px:number,py:number}[],project:(lon:number,lat:number)=>[number,number]}} */
export function satelliteGrid(z = 4, bbox = BBOX) {
  const n = 2 ** z;
  const x0 = Math.floor(worldX(bbox.lonMin) * n), x1 = Math.floor(worldX(bbox.lonMax) * n);
  const y0 = Math.floor(worldY(bbox.latMax) * n), y1 = Math.floor(worldY(bbox.latMin) * n);
  const tiles = [];
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) tiles.push({ x, y, px: (x - x0) * TILE, py: (y - y0) * TILE });
  return {
    width: (x1 - x0 + 1) * TILE,
    height: (y1 - y0 + 1) * TILE,
    tiles,
    project: (lon, lat) => [worldX(lon) * n * TILE - x0 * TILE, worldY(lat) * n * TILE - y0 * TILE],
  };
}

// --- Vue navigable (zoom / déplacement) --------------------------------------------------------
// view = { lon, lat, zoom, w, h } : centre géographique, niveau de zoom (réel, 2,5 à 8) et taille d'écran en px.

export const ZOOM_MIN = 2.6;
export const ZOOM_MAX = 8;
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

export const worldSize = (zoom) => TILE * 2 ** zoom;

export function toScreen(lon, lat, view) {
  const size = worldSize(view.zoom);
  return [(worldX(lon) - worldX(view.lon)) * size + view.w / 2, (worldY(lat) - worldY(view.lat)) * size + view.h / 2];
}

export function fromScreen(sx, sy, view) {
  const size = worldSize(view.zoom);
  const wx = worldX(view.lon) + (sx - view.w / 2) / size;
  const wy = worldY(view.lat) + (sy - view.h / 2) / size;
  const lat = (Math.atan(Math.sinh(Math.PI * (1 - 2 * wy))) * 180) / Math.PI;
  return [wx * 360 - 180, lat];
}

/** Garde le centre dans l'emprise africaine (avec une marge) et le zoom dans ses bornes. */
export function clampView(view, bbox = BBOX) {
  const zoom = clamp(view.zoom, ZOOM_MIN, ZOOM_MAX);
  return { ...view, zoom, lon: clamp(view.lon, bbox.lonMin, bbox.lonMax), lat: clamp(view.lat, bbox.latMin, bbox.latMax) };
}

/** Zoom multiplicatif autour d'un point d'écran : le point sous le curseur reste fixe. */
export function zoomAt(view, factor, sx, sy) {
  const zoom = clamp(view.zoom + Math.log2(factor), ZOOM_MIN, ZOOM_MAX);
  if (zoom === view.zoom) return view;
  const [lon, lat] = fromScreen(sx, sy, view);
  const next = { ...view, zoom };
  const [nx, ny] = toScreen(lon, lat, next);
  const size = worldSize(zoom);
  const cx = worldX(view.lon) + (nx - sx) / size, cy = worldY(view.lat) + (ny - sy) / size;
  const [clon, clat] = fromScreen(view.w / 2 + (cx - worldX(view.lon)) * size, view.h / 2 + (cy - worldY(view.lat)) * size, next);
  return clampView({ ...next, lon: clon, lat: clat });
}

export function panBy(view, dx, dy) {
  const [lon, lat] = fromScreen(view.w / 2 - dx, view.h / 2 - dy, view);
  return clampView({ ...view, lon, lat });
}

/** Vue qui cadre toute l'Afrique dans (w, h). */
export function fitAfrica(w, h, bbox = BBOX) {
  const span = (z) => {
    const size = worldSize(z);
    return [(worldX(bbox.lonMax) - worldX(bbox.lonMin)) * size, (worldY(bbox.latMin) - worldY(bbox.latMax)) * size];
  };
  let zoom = ZOOM_MAX;
  while (zoom > ZOOM_MIN && (span(zoom)[0] > w * 0.96 || span(zoom)[1] > h * 0.96)) zoom -= 0.05;
  return { lon: (bbox.lonMin + bbox.lonMax) / 2, lat: (bbox.latMin + bbox.latMax) / 2 - 1, zoom, w, h };
}

/** Tuiles à afficher : niveau entier le plus proche, couvrant l'écran. */
export function visibleTiles(view, level = Math.round(view.zoom)) {
  const z = clamp(level, 0, 8);
  const n = 2 ** z, scale = worldSize(view.zoom) / (n * TILE);
  const [lon0, lat0] = fromScreen(0, 0, view), [lon1, lat1] = fromScreen(view.w, view.h, view);
  const x0 = Math.floor(worldX(lon0) * n), x1 = Math.floor(worldX(lon1) * n);
  const y0 = Math.max(0, Math.floor(worldY(lat0) * n)), y1 = Math.min(n - 1, Math.floor(worldY(lat1) * n));
  const tiles = [];
  for (let y = y0; y <= y1; y++) {
    for (let xr = x0; xr <= x1; xr++) {
      const x = ((xr % n) + n) % n;
      const [sx, sy] = toScreen(((xr / n) * 360) - 180, (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / n))) * 180) / Math.PI, view);
      tiles.push({ z, x, y, sx, sy, size: TILE * scale, key: `${z}/${x}/${y}` });
    }
  }
  return tiles;
}
