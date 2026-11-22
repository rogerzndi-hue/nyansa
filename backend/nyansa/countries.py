"""Les 54 pays africains (membres de l'ONU) + le Sahara occidental (non évalué).

Découpage régional de l'Union africaine. Chaque pays porte ses noms en FR, EN, PT et AR,
des villes (poids réduit au géocodage) et des alias. Source des codes : ISO 3166-1.
"""

from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import Region


@dataclass(frozen=True)
class Country:
    iso3: str
    iso2: str
    region: Region
    name_fr: str
    name_en: str
    name_pt: str
    name_ar: str
    cities: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    rated: bool = True


N, W, C, E, S = Region.NORTH, Region.WEST, Region.CENTRAL, Region.EAST, Region.SOUTHERN

COUNTRIES: tuple[Country, ...] = (
    # Afrique du Nord
    Country("MAR", "MA", N, "Maroc", "Morocco", "Marrocos", "المغرب", ("Rabat", "Casablanca")),
    Country("DZA", "DZ", N, "Algérie", "Algeria", "Argélia", "الجزائر", ("Alger", "Algiers")),
    Country("TUN", "TN", N, "Tunisie", "Tunisia", "Tunísia", "تونس", ("Tunis",)),
    Country("LBY", "LY", N, "Libye", "Libya", "Líbia", "ليبيا", ("Benghazi", "Misrata")),
    Country("EGY", "EG", N, "Égypte", "Egypt", "Egito", "مصر", ("Le Caire", "Cairo")),
    Country("MRT", "MR", N, "Mauritanie", "Mauritania", "Mauritânia", "موريتانيا", ("Nouakchott",)),
    # Afrique de l'Ouest
    Country("MLI", "ML", W, "Mali", "Mali", "Mali", "مالي", ("Bamako", "Tombouctou", "Kidal", "Ménaka")),
    Country("NER", "NE", W, "Niger", "Niger", "Níger", "النيجر", ("Niamey", "Agadez")),
    Country("SEN", "SN", W, "Sénégal", "Senegal", "Senegal", "السنغال", ("Dakar",)),
    Country("GMB", "GM", W, "Gambie", "Gambia", "Gâmbia", "غامبيا", ("Banjul",)),
    Country("GNB", "GW", W, "Guinée-Bissau", "Guinea-Bissau", "Guiné-Bissau", "غينيا بيساو", ("Bissau",)),
    Country("GIN", "GN", W, "Guinée", "Guinea", "Guiné", "غينيا", ("Conakry",)),
    Country("SLE", "SL", W, "Sierra Leone", "Sierra Leone", "Serra Leoa", "سيراليون", ("Freetown",)),
    Country("LBR", "LR", W, "Libéria", "Liberia", "Libéria", "ليبيريا", ("Monrovia",)),
    Country(
        "CIV", "CI", W, "Côte d'Ivoire", "Ivory Coast", "Costa do Marfim", "ساحل العاج",
        ("Abidjan", "Yamoussoukro"), ("كوت ديفوار",),
    ),
    Country("BFA", "BF", W, "Burkina Faso", "Burkina Faso", "Burkina Faso", "بوركينا فاسو", ("Ouagadougou",)),
    Country("GHA", "GH", W, "Ghana", "Ghana", "Gana", "غانا", ("Accra",)),
    Country("TGO", "TG", W, "Togo", "Togo", "Togo", "توغو", ("Lomé",)),
    Country("BEN", "BJ", W, "Bénin", "Benin", "Benim", "بنين", ("Cotonou", "Porto-Novo")),
    Country(
        "NGA", "NG", W, "Nigeria", "Nigeria", "Nigéria", "نيجيريا",
        ("Abuja", "Lagos", "Kano", "Maiduguri", "Benin City"), ("Delta du Niger", "Niger Delta"),
    ),
    Country("CPV", "CV", W, "Cap-Vert", "Cape Verde", "Cabo Verde", "الرأس الأخضر"),
    # Afrique centrale
    Country("TCD", "TD", C, "Tchad", "Chad", "Chade", "تشاد", ("N'Djamena",)),
    Country("CMR", "CM", C, "Cameroun", "Cameroon", "Camarões", "الكاميرون", ("Yaoundé", "Douala")),
    Country(
        "CAF", "CF", C, "Centrafrique", "Central African Republic", "República Centro-Africana",
        "أفريقيا الوسطى", ("Bangui",), ("République centrafricaine", "جمهورية أفريقيا الوسطى"),
    ),
    Country("GNQ", "GQ", C, "Guinée équatoriale", "Equatorial Guinea", "Guiné Equatorial", "غينيا الاستوائية", ("Malabo",)),
    Country("GAB", "GA", C, "Gabon", "Gabon", "Gabão", "الغابون", ("Libreville",)),
    Country(
        "COG", "CG", C, "Congo-Brazzaville", "Republic of the Congo", "República do Congo",
        "جمهورية الكونغو", ("Brazzaville", "Pointe-Noire"), ("République du Congo",),
    ),
    Country(
        "COD", "CD", C, "République démocratique du Congo", "Democratic Republic of the Congo",
        "República Democrática do Congo", "جمهورية الكونغو الديمقراطية",
        ("Kinshasa", "Goma", "Lubumbashi", "Kivu"),
        ("RDC", "RD Congo", "DR Congo", "DRC", "Congo-Kinshasa"),
    ),
    Country("STP", "ST", C, "Sao Tomé-et-Principe", "Sao Tome and Principe", "São Tomé e Príncipe", "ساو تومي وبرينسيبي", ("Sao Tomé",)),
    Country("BDI", "BI", C, "Burundi", "Burundi", "Burúndi", "بوروندي", ("Bujumbura", "Gitega")),
    # Afrique de l'Est
    Country("SDN", "SD", E, "Soudan", "Sudan", "Sudão", "السودان", ("Khartoum", "El Fasher", "Darfour", "Darfur", "Port-Soudan")),
    Country("SSD", "SS", E, "Soudan du Sud", "South Sudan", "Sudão do Sul", "جنوب السودان", ("Juba",)),
    Country("ERI", "ER", E, "Érythrée", "Eritrea", "Eritreia", "إريتريا", ("Asmara",)),
    Country("ETH", "ET", E, "Éthiopie", "Ethiopia", "Etiópia", "إثيوبيا", ("Addis-Abeba", "Addis Ababa", "Tigré", "Tigray")),
    Country("DJI", "DJ", E, "Djibouti", "Djibouti", "Djibuti", "جيبوتي"),
    Country("SOM", "SO", E, "Somalie", "Somalia", "Somália", "الصومال", ("Mogadiscio", "Mogadishu")),
    Country("UGA", "UG", E, "Ouganda", "Uganda", "Uganda", "أوغندا", ("Kampala",)),
    Country("KEN", "KE", E, "Kenya", "Kenya", "Quénia", "كينيا", ("Nairobi", "Mombasa"), ("Quênia",)),
    Country("RWA", "RW", E, "Rwanda", "Rwanda", "Ruanda", "رواندا", ("Kigali",)),
    Country("TZA", "TZ", E, "Tanzanie", "Tanzania", "Tanzânia", "تنزانيا", ("Dodoma", "Dar es Salaam", "Zanzibar")),
    Country("SYC", "SC", E, "Seychelles", "Seychelles", "Seicheles", "سيشل"),
    Country("COM", "KM", E, "Comores", "Comoros", "Comores", "جزر القمر", ("Moroni",)),
    Country("MUS", "MU", E, "Île Maurice", "Mauritius", "Maurícia", "موريشيوس", ("Port-Louis",), ("République de Maurice",)),
    Country("MDG", "MG", E, "Madagascar", "Madagascar", "Madagáscar", "مدغشقر", ("Antananarivo",)),
    # Afrique australe
    Country("AGO", "AO", S, "Angola", "Angola", "Angola", "أنغولا", ("Luanda",)),
    Country("ZMB", "ZM", S, "Zambie", "Zambia", "Zâmbia", "زامبيا", ("Lusaka",)),
    Country("MWI", "MW", S, "Malawi", "Malawi", "Maláui", "ملاوي", ("Lilongwe",)),
    Country("MOZ", "MZ", S, "Mozambique", "Mozambique", "Moçambique", "موزمبيق", ("Maputo", "Cabo Delgado")),
    Country("ZWE", "ZW", S, "Zimbabwe", "Zimbabwe", "Zimbábue", "زيمبابوي", ("Harare",)),
    Country("BWA", "BW", S, "Botswana", "Botswana", "Botsuana", "بوتسوانا", ("Gaborone",)),
    Country("NAM", "NA", S, "Namibie", "Namibia", "Namíbia", "ناميبيا", ("Windhoek",)),
    Country("ZAF", "ZA", S, "Afrique du Sud", "South Africa", "África do Sul", "جنوب أفريقيا", ("Pretoria", "Johannesburg", "Cape Town")),
    Country("LSO", "LS", S, "Lesotho", "Lesotho", "Lesoto", "ليسوتو", ("Maseru",)),
    Country("SWZ", "SZ", S, "Eswatini", "Eswatini", "Essuatíni", "إسواتيني", ("Mbabane",), ("Swaziland", "Souaziland")),
    # Territoire non autonome, statut non réglé : géocodé mais jamais noté.
    Country(
        "ESH", "EH", N, "Sahara occidental", "Western Sahara", "Saara Ocidental", "الصحراء الغربية",
        ("Laâyoune", "El Aaiun"), rated=False,
    ),
)

COUNTRIES_BY_ISO: dict[str, Country] = {c.iso3: c for c in COUNTRIES}
RATED_COUNTRIES: tuple[Country, ...] = tuple(c for c in COUNTRIES if c.rated)
