"""Cycle de vie d'un livrable : versions, contrôles, validation par une seconde personne, persistance JSON.

Brouillon -> En validation -> Validé -> Archivé. Seul un brouillon est modifiable. Les contrôles bloquent
l'envoi en validation : aucun fait sans source, aucune section d'analyse vide, auteur et organisation renseignés.
Les blocs calculés à la construction (auto) ne peuvent pas être réécrits : ils peuvent seulement être retirés.

Identité : l'acteur et le rôle sont fournis par l'appelant. Le serveur de développement les prend dans des
en-têtes non authentifiés ; en production ils viendront d'OIDC. Ne jamais exposer ce mode hors poste local.
"""

from __future__ import annotations

import copy
import difflib
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .catalog import CLASSIFICATIONS, KINDS, SECTIONS
from ..persistence import atomic_write_json

ROLE_EDIT = frozenset({"analyst", "admin"})
ROLE_VALIDATE = frozenset({"validator", "manager", "admin"})
ROLE_READ_DRAFT = ROLE_EDIT | ROLE_VALIDATE
ROLE_READ_FINAL = ROLE_READ_DRAFT | {"executive"}  # un décideur ne voit que les documents validés
MAX_BLOCKS, MAX_TEXT, MAX_TITLE = 600, 20_000, 300
MANDATORY_SECTIONS = ("sources",)


class ReportError(Exception):
    status = 400

    def __init__(self, message: str, issues: list[dict] | None = None) -> None:
        super().__init__(message)
        self.issues = issues or []


class NotFound(ReportError):
    status = 404


class Forbidden(ReportError):
    status = 403


class Conflict(ReportError):
    status = 409


class Invalid(ReportError):
    status = 422


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_report(report: dict) -> list[dict]:
    """Contrôles avant validation. Chaque anomalie : {severity: error|warning, section, block, message}."""
    issues: list[dict] = []
    fr = report.get("language", "fr") == "fr"

    def add(severity: str, message: str, section: str | None = None, block: str | None = None) -> None:
        issues.append({"severity": severity, "section": section, "block": block, "message": message})

    meta = report.get("meta", {})
    if not meta.get("analyst"):
        add("error", "Nom de l'analyste non renseigné")
    if not meta.get("organization"):
        add("error", "Organisation non renseignée")
    if not report.get("title"):
        add("error", "Titre vide")
    n_sources = len(report.get("sources", []))
    social_only = 0
    for sec in report.get("sections", []):
        if sec.get("mode") == "analyst" and not sec.get("na"):
            if not any(b["kind"] != "heading" and b["text"].strip() for b in sec["blocks"]):
                add("error", f"Section « {sec['title']} » vide : à rédiger ou à marquer « sans objet »", sec["key"])
        for b in sec["blocks"]:
            refs = b.get("refs", [])
            if b["kind"] == "fact" and not refs:
                add("error", "Fait sans source : ajouter une référence ou le requalifier (estimation, hypothèse)", sec["key"], b["id"])
            if any(not isinstance(n, int) or n < 1 or n > n_sources for n in refs):
                add("error", "Référence vers une source inexistante", sec["key"], b["id"])
            if b.get("status") == "unconfirmed_social":
                social_only += 1
    if social_only:
        add("warning", f"{social_only} fait(s) reposent uniquement sur des réseaux sociaux : ils restent étiquetés « non confirmé »" if fr
            else f"{social_only} fact(s) rely on social media only: they stay labelled 'unconfirmed'")
    for w in report.get("warnings", []):
        add("warning", w)
    if meta.get("demo"):
        add("warning", "Données fictives de démonstration : filigrane maintenu sur tous les exports")
    return issues


def _block_equal(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("kind", "text", "refs", "status", "head", "rows", "event_id"))


def _clean_sections(old: list[dict], new: object) -> list[dict]:
    if not isinstance(new, list):
        raise Invalid("sections : liste attendue")
    old_by_key = {s["key"]: s for s in old}
    old_blocks = {b["id"]: b for s in old for b in s["blocks"]}
    out: list[dict] = []
    seen_keys: set[str] = set()
    total = 0
    for sec in new:
        if not isinstance(sec, dict) or not isinstance(sec.get("key"), str):
            raise Invalid("section invalide")
        key = sec["key"]
        if key in seen_keys:
            raise Invalid(f"Section en double : {key}")
        seen_keys.add(key)
        previous = old_by_key.get(key)
        if previous is None and not key.startswith("custom:"):
            raise Invalid(f"Section inconnue : {key}")
        title = previous["title"] if previous else str(sec.get("title") or "").strip()[:120]
        if not title:
            raise Invalid("Titre de section requis")
        blocks = []
        for b in sec.get("blocks", []):
            if not isinstance(b, dict) or not isinstance(b.get("id"), str):
                raise Invalid("bloc invalide")
            total += 1
            if total > MAX_BLOCKS:
                raise Invalid(f"Trop de blocs (max {MAX_BLOCKS})")
            known = old_blocks.get(b["id"])
            if known is not None and known.get("auto"):
                if not _block_equal(known, b):
                    raise Invalid("Un bloc calculé ne peut pas être modifié (il peut être retiré) : " + known["text"][:60])
                blocks.append(copy.deepcopy(known))
                continue
            kind, text = b.get("kind"), b.get("text", "")
            if kind not in (*KINDS, "heading") or not isinstance(text, str) or len(text) > MAX_TEXT:
                raise Invalid("Bloc rédigé invalide (type ou longueur)")
            refs = b.get("refs", [])
            if not isinstance(refs, list) or any(not isinstance(n, int) or isinstance(n, bool) for n in refs):
                raise Invalid("refs : liste d'entiers attendue")
            blocks.append({"id": b["id"] if known else uuid.uuid4().hex[:10], "kind": kind, "text": text, "refs": sorted(set(refs)), "auto": False})
        mode = previous["mode"] if previous else "analyst"
        if mode == "auto" and key in MANDATORY_SECTIONS:
            kept = {b["id"] for b in blocks}
            if {b["id"] for b in previous["blocks"]} - kept:
                raise Invalid("La section Sources ne peut pas être modifiée")
        out.append({"key": key, "title": title, "mode": mode, "guide": previous["guide"] if previous else "", "na": bool(sec.get("na")) and mode == "analyst", "blocks": blocks})
    for key in MANDATORY_SECTIONS:
        if key not in seen_keys:
            raise Invalid("La section Sources est obligatoire")
    return out


def _lines(sections: list[dict]) -> dict[str, tuple[str, list[str]]]:
    res = {}
    for s in sections:
        lines = []
        for b in s["blocks"]:
            if b["kind"] == "table":
                lines.append(f"[tableau] {len(b.get('rows', []))} ligne(s)")
            else:
                refs = "".join(f"[{n}]" for n in b.get("refs", []))
                lines.append(f"[{b['kind']}] {b['text']} {refs}".rstrip())
        if s.get("na"):
            lines.append("[sans objet]")
        res[s["key"]] = (s["title"], lines)
    return res


class ReportStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._path = Path(path) if path else None
        self._reports: dict[str, dict] = {}
        if self._path and self._path.exists():
            try:
                self._reports = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"Fichier des livrables illisible ({self._path}) : {exc}. Corrigez ou déplacez-le ; rien n'a été écrasé.") from exc

    @property
    def persistent(self) -> bool:
        return self._path is not None

    # --- persistance ---
    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path, self._reports)

    # --- accès ---
    def _save_or_restore(self, previous: dict[str, dict]) -> None:
        try:
            self._save()
        except BaseException:
            self._reports = previous
            raise

    def _get(self, report_id: str) -> dict:
        report = self._reports.get(report_id)
        if report is None:
            raise NotFound("Livrable introuvable")
        return report

    @staticmethod
    def _can_read(report: dict, role: str) -> bool:
        if report["status"] in ("validated", "archived"):
            return role in ROLE_READ_FINAL
        return role in ROLE_READ_DRAFT

    def _audit(self, report: dict, actor: str, role: str, action: str, detail: str = "") -> None:
        report["audit"].append({"at": _now(), "actor": actor, "role": role, "action": action, "detail": detail})

    def create(self, built: dict, actor: str, role: str) -> dict:
        if role not in ROLE_EDIT:
            raise Forbidden("Rôle sans droit de création")
        now = _now()
        report = {
            **copy.deepcopy(built), "id": uuid.uuid4().hex, "status": "draft", "version": 1, "created_by": actor, "created_at": now,
            "updated_at": now, "last_editor": actor, "validated_by": None, "validated_at": None, "audit": [], "versions": [],
        }
        report["versions"].append({"version": 1, "at": now, "actor": actor, "note": "Brouillon généré", "title": report["title"], "sections": copy.deepcopy(report["sections"])})
        self._audit(report, actor, role, "create", report["template_id"])
        with self._lock:
            previous = copy.deepcopy(self._reports)
            self._reports[report["id"]] = report
            self._save_or_restore(previous)
        return copy.deepcopy(report)

    def get(self, report_id: str, role: str) -> dict:
        with self._lock:
            report = self._get(report_id)
            if not self._can_read(report, role):
                raise Forbidden("Ce document n'est pas accessible avec ce rôle")
            return copy.deepcopy(report)

    def list(self, role: str, *, status: str | None = None, template_id: str | None = None, q: str | None = None) -> list[dict]:
        with self._lock:
            rows = []
            for r in self._reports.values():
                if not self._can_read(r, role) or (status and r["status"] != status) or (template_id and r["template_id"] != template_id):
                    continue
                if q and q.lower() not in r["title"].lower():
                    continue
                rows.append({k: r[k] for k in ("id", "title", "template_id", "status", "version", "language", "updated_at", "created_by")}
                            | {"classification": r["meta"]["classification"], "analyst": r["meta"]["analyst"], "demo": bool(r["meta"].get("demo"))})
            return sorted(rows, key=lambda r: r["updated_at"], reverse=True)

    # --- modification ---
    def update(self, report_id: str, actor: str, role: str, *, expected_version: int, title: str | None = None,
               meta: dict | None = None, sections: object = None, note: str = "") -> dict:
        if role not in ROLE_EDIT:
            raise Forbidden("Rôle sans droit de modification")
        with self._lock:
            report = self._get(report_id)
            if report["status"] != "draft":
                raise Conflict("Document verrouillé (en validation, validé ou archivé) : le rejeter ou le dupliquer pour le modifier")
            if expected_version != report["version"]:
                raise Conflict(f"Le document est en version {report['version']} (vous éditiez la {expected_version}) : rechargez avant d'enregistrer")
            before = json.dumps([report["title"], report["meta"], report["sections"]], sort_keys=True)
            new_title = report["title"] if title is None else str(title).strip()[:MAX_TITLE]
            new_meta = dict(report["meta"])
            for k in ("recipient", "analyst", "organization"):
                if meta and k in meta:
                    new_meta[k] = str(meta[k]).strip()[:200]
            if meta and "classification" in meta:
                if meta["classification"] not in CLASSIFICATIONS:
                    raise Invalid("Confidentialité inconnue")
                new_meta["classification"] = meta["classification"]
            new_sections = report["sections"] if sections is None else _clean_sections(report["sections"], sections)
            if json.dumps([new_title, new_meta, new_sections], sort_keys=True) == before:
                return copy.deepcopy(report)  # rien n'a changé : pas de nouvelle version
            previous = copy.deepcopy(self._reports)
            report.update(title=new_title, meta=new_meta, sections=new_sections, version=report["version"] + 1, updated_at=_now(), last_editor=actor)
            report["versions"].append({"version": report["version"], "at": report["updated_at"], "actor": actor, "note": note[:200], "title": new_title, "sections": copy.deepcopy(new_sections)})
            self._audit(report, actor, role, "edit", f"v{report['version']}")
            self._save_or_restore(previous)
            return copy.deepcopy(report)

    def transition(self, report_id: str, action: str, actor: str, role: str, comment: str = "") -> dict:
        with self._lock:
            report = self._get(report_id)
            status = report["status"]
            previous = copy.deepcopy(self._reports)
            if action == "submit":
                if role not in ROLE_EDIT:
                    raise Forbidden("Rôle sans droit d'envoi en validation")
                if status != "draft":
                    raise Conflict("Seul un brouillon peut être soumis")
                issues = check_report(report)
                errors = [i for i in issues if i["severity"] == "error"]
                if errors:
                    raise Invalid(f"{len(errors)} point(s) bloquant(s) avant validation", issues)
                report["status"] = "in_review"
            elif action in ("validate", "reject"):
                if role not in ROLE_VALIDATE:
                    raise Forbidden("Rôle sans droit de validation")
                if status != "in_review":
                    raise Conflict("Le document n'est pas en validation")
                if action == "reject":
                    if not comment.strip():
                        raise Invalid("Un motif de rejet est obligatoire")
                    report["status"] = "draft"
                else:
                    if actor in (report["created_by"], report["last_editor"]):
                        raise Forbidden("La validation doit être faite par une autre personne que l'auteur ou le dernier rédacteur")
                    errors = [i for i in check_report(report) if i["severity"] == "error"]
                    if errors:
                        raise Invalid(f"{len(errors)} point(s) bloquant(s)", errors)
                    report.update(status="validated", validated_by=actor, validated_at=_now())
            elif action == "archive":
                if role not in ROLE_EDIT | ROLE_VALIDATE:
                    raise Forbidden("Rôle sans droit d'archivage")
                if status not in ("draft", "validated"):
                    raise Conflict("Archivage impossible dans cet état")
                report["status"] = "archived"
            else:
                raise Invalid(f"Action inconnue : {action}")
            report["updated_at"] = _now()
            self._audit(report, actor, role, action, comment[:300])
            self._save_or_restore(previous)
            return copy.deepcopy(report)

    def duplicate(self, report_id: str, actor: str, role: str) -> dict:
        if role not in ROLE_EDIT:
            raise Forbidden("Rôle sans droit de création")
        with self._lock:
            src = self._get(report_id)
            if not self._can_read(src, role):
                raise Forbidden("Ce document n'est pas accessible avec ce rôle")
            built = {k: copy.deepcopy(src[k]) for k in ("template_id", "title", "language", "params", "meta", "sections", "sources", "warnings", "event_ids")}
            built["title"] = f"{src['title']} (copie)"[:MAX_TITLE]
        return self.create(built, actor, role)

    def record_export(self, report_id: str, actor: str, role: str, fmt: str) -> dict:
        """Contrôle d'accès et trace d'un export ; renvoie le document."""
        with self._lock:
            report = self._get(report_id)
            if not self._can_read(report, role):
                raise Forbidden("Ce document n'est pas accessible avec ce rôle")
            previous = copy.deepcopy(self._reports)
            self._audit(report, actor, role, f"export.{fmt}", f"v{report['version']} {report['status']}")
            self._save_or_restore(previous)
            return copy.deepcopy(report)

    # --- versions ---
    def versions(self, report_id: str, role: str) -> list[dict]:
        report = self.get(report_id, role)
        return [{k: v[k] for k in ("version", "at", "actor", "note")} for v in report["versions"]]

    def diff(self, report_id: str, a: int, b: int, role: str) -> list[dict]:
        report = self.get(report_id, role)
        by_version = {v["version"]: v for v in report["versions"]}
        if a not in by_version or b not in by_version:
            raise NotFound("Version inconnue")
        left, right = _lines(by_version[a]["sections"]), _lines(by_version[b]["sections"])
        out = []
        for key in [*right, *(k for k in left if k not in right)]:
            title_l, lines_l = left.get(key, ("", []))
            title_r, lines_r = right.get(key, ("", []))
            if lines_l == lines_r and key in left and key in right:
                continue
            change = "added" if key not in left else "removed" if key not in right else "modified"
            delta = [ln for ln in difflib.unified_diff(lines_l, lines_r, lineterm="", n=0) if not ln.startswith(("---", "+++", "@@"))]
            out.append({"key": key, "title": title_r or title_l, "change": change, "lines": delta})
        return out
