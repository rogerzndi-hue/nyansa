// Page d'impression d'un livrable : bouton « Imprimer / PDF » (pas de script en ligne : CSP script-src 'self').
document.getElementById("print")?.addEventListener("click", () => window.print());
if (new URLSearchParams(location.search).get("auto") === "1") window.print();
