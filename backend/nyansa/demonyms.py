"""Gentilés FR/EN (pluriel ajouté automatiquement) : « Kenyans », « Maliens », « Nigerian Army »…

Indice un peu plus faible qu'un nom de pays (WEIGHT) : « Kenyans … Ebola » doit donner le Kenya
même si le corps du texte cite l'Ouganda. « Congolais/Congolese » est volontairement absent
(RDC ou Congo-Brazzaville : ambigu, voir geo._AMBIGUOUS_TERMS).
"""

from __future__ import annotations

WEIGHT = 0.75

DEMONYMS: dict[str, tuple[str, ...]] = {
    "MAR": ("Marocain", "Moroccan"), "DZA": ("Algérien", "Algerian"), "TUN": ("Tunisien", "Tunisian"),
    "LBY": ("Libyen", "Libyan"), "EGY": ("Égyptien", "Egyptian"), "MRT": ("Mauritanien", "Mauritanian"),
    "MLI": ("Malien", "Malian"), "NER": ("Nigérien", "Nigerien"), "BFA": ("Burkinabè", "Burkinabe", "Burkinabé"),
    "SEN": ("Sénégalais", "Senegalese"), "GMB": ("Gambien", "Gambian"), "GNB": ("Bissau-Guinéen", "Bissau-Guinean"),
    "GIN": ("Guinéen", "Guinean"), "SLE": ("Sierra-Léonais", "Sierra Leonean"), "LBR": ("Libérien", "Liberian"),
    "CIV": ("Ivoirien", "Ivorian"), "GHA": ("Ghanéen", "Ghanaian"), "TGO": ("Togolais", "Togolese"),
    "BEN": ("Béninois", "Beninese"), "NGA": ("Nigérian", "Nigerian"), "CPV": ("Cap-Verdien", "Cape Verdean"),
    "TCD": ("Tchadien", "Chadian"), "CMR": ("Camerounais", "Cameroonian"), "CAF": ("Centrafricain", "Central African"),
    "GNQ": ("Équato-Guinéen", "Equatorial Guinean"), "GAB": ("Gabonais", "Gabonese"),
    "AGO": ("Angolais", "Angolan"), "SDN": ("Soudanais", "Sudanese"), "SSD": ("Sud-Soudanais", "South Sudanese"),
    "ETH": ("Éthiopien", "Ethiopian"), "ERI": ("Érythréen", "Eritrean"), "DJI": ("Djiboutien", "Djiboutian"),
    "SOM": ("Somalien", "Somali"), "KEN": ("Kényan", "Kenyan"), "UGA": ("Ougandais", "Ugandan"),
    "RWA": ("Rwandais", "Rwandan"), "BDI": ("Burundais", "Burundian"), "TZA": ("Tanzanien", "Tanzanian"),
    "SYC": ("Seychellois", "Seychellois"), "COM": ("Comorien", "Comorian"), "MDG": ("Malgache", "Malagasy"),
    "MUS": ("Mauricien", "Mauritian"), "MWI": ("Malawite", "Malawian"), "MOZ": ("Mozambicain", "Mozambican"),
    "ZMB": ("Zambien", "Zambian"), "ZWE": ("Zimbabwéen", "Zimbabwean"), "BWA": ("Botswanais", "Motswana"),
    "NAM": ("Namibien", "Namibian"), "ZAF": ("Sud-Africain", "South African"), "LSO": ("Lesothan", "Basotho"),
    "SWZ": ("Swazi",), "STP": ("Santoméen", "Sao Tomean"),
}


def forms(iso3: str) -> tuple[str, ...]:
    """Singuliers + pluriels réguliers (les formes déjà en -s ou -i restent telles quelles)."""
    out: list[str] = []
    for word in DEMONYMS.get(iso3, ()):
        out.append(word)
        if not word.endswith(("s", "i")):
            out.append(word + "s")
    return tuple(out)
