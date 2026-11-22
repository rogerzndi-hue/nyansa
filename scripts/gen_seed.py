"""Génère db/seed_countries.sql à partir de backend/nyansa/countries.py.

Usage (depuis la racine du projet) : python3 scripts/gen_seed.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from nyansa.countries import COUNTRIES  # noqa: E402


def sql(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    rows = ",\n".join(
        f"  ({sql(c.iso3)}, {sql(c.iso2)}, {sql(c.name_fr)}, {sql(c.name_en)}, "
        f"{sql(c.region.value)}, {'true' if c.rated else 'false'})"
        for c in COUNTRIES
    )
    out = (
        "-- Généré par scripts/gen_seed.py : ne pas modifier à la main.\n"
        "INSERT INTO country (iso3, iso2, name_fr, name_en, region, rated) VALUES\n"
        f"{rows}\n"
        "ON CONFLICT (iso3) DO NOTHING;\n"
    )
    target = ROOT / "db" / "seed_countries.sql"
    target.write_text(out, encoding="utf-8")
    print(f"{len(COUNTRIES)} pays écrits dans {target}")


if __name__ == "__main__":
    main()
