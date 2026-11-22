export const LEVELS = [
  { key: "low", label: "Faible" }, { key: "moderate", label: "Modéré" },
  { key: "high", label: "Élevé" }, { key: "critical", label: "Critique" },
];
/** Mêmes seuils que backend/nyansa/taxonomy.py::risk_level. */
export function levelOf(score) {
  return score < 35 ? "low" : score < 55 ? "moderate" : score < 75 ? "high" : "critical";
}
