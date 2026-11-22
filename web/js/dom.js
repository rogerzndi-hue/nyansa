/** Création de nœuds DOM sans innerHTML : les titres d'événements viennent de sources externes (XSS). */
export function h(tag, attrs = {}, ...children) {
  const svg = ["svg", "circle", "text", "g", "title", "image", "rect", "line", "path"].includes(tag) && attrs.__svg;
  const el = svg ? document.createElementNS("http://www.w3.org/2000/svg", tag) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "__svg" || v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) if (c != null) el.append(c);
  return el;
}
export const svgEl = (tag, attrs = {}, ...ch) => h(tag, { ...attrs, __svg: true }, ...ch);
export const clear = (el) => el.replaceChildren();
