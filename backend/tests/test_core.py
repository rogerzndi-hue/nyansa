"""Tests du noyau : pays, taxonomie, langue, Admiralty, classification."""

from __future__ import annotations

import unittest
from collections import Counter

from nyansa.admiralty import parse_rating, probability
from nyansa.classify import classify
from nyansa.countries import COUNTRIES, COUNTRIES_BY_ISO, RATED_COUNTRIES
from nyansa.geo import extract_countries
from nyansa.lang import detect_language
from nyansa.taxonomy import Domain, Region, criticality_level, risk_level
from nyansa.text import fold, strip_html


class CountriesTest(unittest.TestCase):
    def test_54_rated_countries_and_codes_unique(self):
        self.assertEqual(len(RATED_COUNTRIES), 54)
        self.assertEqual(len({c.iso3 for c in COUNTRIES}), len(COUNTRIES))
        self.assertEqual(len({c.iso2 for c in COUNTRIES}), len(COUNTRIES))

    def test_region_sizes_follow_african_union(self):
        sizes = Counter(c.region for c in RATED_COUNTRIES)
        self.assertEqual(
            sizes,
            {Region.NORTH: 6, Region.WEST: 15, Region.CENTRAL: 9, Region.EAST: 14, Region.SOUTHERN: 10},
        )
        self.assertEqual(COUNTRIES_BY_ISO["MRT"].region, Region.NORTH)

    def test_western_sahara_is_geocoded_but_never_rated(self):
        self.assertFalse(COUNTRIES_BY_ISO["ESH"].rated)
        self.assertNotIn("ESH", {c.iso3 for c in RATED_COUNTRIES})


class TaxonomyTest(unittest.TestCase):
    def test_criticality_levels(self):
        self.assertEqual(
            [criticality_level(s) for s in (1, 4, 5, 9, 10, 14, 15, 25)],
            ["low", "low", "moderate", "moderate", "high", "high", "critical", "critical"],
        )

    def test_risk_levels(self):
        self.assertEqual(
            [risk_level(s) for s in (0, 34.9, 35, 54.9, 55, 74.9, 75, 100)],
            ["low", "low", "moderate", "moderate", "high", "high", "critical", "critical"],
        )


class TextTest(unittest.TestCase):
    def test_fold_removes_accents_and_punctuation(self):
        self.assertEqual(fold("Côte d’Ivoire — Éthiopie!"), "cote d ivoire ethiopie")

    def test_fold_normalizes_arabic(self):
        self.assertEqual(fold("إثيوبيا"), fold("اثيوبيا"))

    def test_strip_html(self):
        self.assertEqual(strip_html("<p>Bonjour <b>le</b> monde</p><script>x()</script>"), "Bonjour le monde")


class AdmiraltyTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_rating("b2"), ("B", 2))
        for bad in ("G1", "A7", "A", "11", ""):
            with self.assertRaises(ValueError):
                parse_rating(bad)

    def test_probability_rises_with_credibility_and_drops_for_weak_sources(self):
        self.assertEqual([probability("B", c) for c in (1, 2, 3, 4, 5)], [5, 4, 3, 2, 1])
        self.assertEqual(probability("D", 3), 2)
        self.assertEqual(probability("E", 5), 1)


class LanguageTest(unittest.TestCase):
    def test_detects_fr_en_pt_ar(self):
        self.assertEqual(detect_language("Les autorités ont annoncé que le scrutin est reporté dans les régions du nord.")[0], "fr")
        self.assertEqual(detect_language("The government has announced that the vote is postponed in the northern regions of the country.")[0], "en")
        self.assertEqual(detect_language("O governo anunciou que as eleições foram adiadas para as regiões do norte e para as cidades.")[0], "pt")
        self.assertEqual(detect_language("أعلنت الحكومة تأجيل الانتخابات في المناطق الشمالية")[0], "ar")

    def test_unknown_when_too_little_signal(self):
        self.assertEqual(detect_language("Mali 2024")[0], "und")
        self.assertEqual(detect_language("")[0], "und")


class GeoTest(unittest.TestCase):
    def iso(self, title, body=""):
        return extract_countries(title, body).primary

    def test_niger_is_not_nigeria(self):
        self.assertEqual(self.iso("Attaque près de Niamey, au Niger"), "NER")
        self.assertEqual(self.iso("Explosion à Abuja, Nigeria"), "NGA")

    def test_longest_match_wins(self):
        self.assertEqual(self.iso("Combats au Soudan du Sud"), "SSD")
        self.assertEqual(self.iso("Combats au Soudan"), "SDN")
        self.assertEqual(self.iso("Réfugiés en Guinée-Bissau"), "GNB")
        self.assertEqual(self.iso("Visite en Guinée équatoriale"), "GNQ")
        self.assertEqual(self.iso("Réunion à Conakry, en Guinée"), "GIN")

    def test_apostrophes_and_languages(self):
        for text in ("Côte d'Ivoire", "Côte d’Ivoire", "Cote d Ivoire", "Ivory Coast", "Costa do Marfim", "ساحل العاج"):
            self.assertEqual(self.iso(f"Élections en {text}"), "CIV", text)
        self.assertEqual(self.iso("هجوم في جنوب السودان"), "SSD")

    def test_trap_phrases_are_not_countries(self):
        self.assertIsNone(self.iso("Piraterie dans le golfe de Guinée"))
        self.assertIsNone(self.iso("Pollution du bassin du Congo"))

    def test_congo_alone_is_flagged_ambiguous(self):
        result = extract_countries("Attaque dans l'est du Congo")
        self.assertIsNone(result.primary)
        self.assertEqual(result.ambiguous, ("congo",))
        self.assertEqual(self.iso("Épidémie en RDC"), "COD")
        self.assertEqual(self.iso("Manifestation à Brazzaville"), "COG")

    def test_title_and_city_weights_pick_the_main_country(self):
        result = extract_countries("Accord entre le Ghana et le Togo", "Le Togo, le Togo et le Ghana signent.")
        self.assertEqual(result.hits[0].iso3, "TGO")
        self.assertEqual(result.secondary(), ("GHA",))
        self.assertEqual(self.iso("Tensions à Bamako"), "MLI")

    def test_stop_phrase_does_not_hide_a_real_country_mention(self):
        self.assertEqual(self.iso("Piraterie dans le golfe de Guinée, au large du Nigeria"), "NGA")

    def test_substrings_never_match(self):
        self.assertIsNone(self.iso("Le malin et la malice"))
        self.assertEqual(self.iso("Situation en Somalie"), "SOM")  # et non "Mali" dans "Somalie"


class ClassifyTest(unittest.TestCase):
    def test_multiword_wildcards_match_each_word(self):
        titles = (
            "Tension communautaire dans le nord du Mali",
            "Tensions communautaires dans le nord du Mali",
            "Des tensions communautaires persistent",
        )
        for title in titles:
            with self.subTest(title=title):
                result = classify(title, "")
                self.assertIsNotNone(result)
                self.assertEqual(
                    (result.domain, result.event_type),
                    (Domain.SOC, "communal_tension"),
                )

    def test_security_fr(self):
        c = classify("Attaque armée contre un convoi", "Des hommes armés ont attaqué un convoi à la sortie de la ville.")
        self.assertEqual((c.domain, c.event_type, c.impact), (Domain.SEC, "armed_attack", 4))
        self.assertIn("attaque", c.evidence)

    def test_ransomware_beats_generic_attack(self):
        c = classify("Ransomware attack hits port authority", "")
        self.assertEqual((c.domain, c.event_type), (Domain.CYB, "ransomware"))

    def test_health_pt_and_arabic(self):
        c = classify("Epidemia de cólera no Malawi", "")
        self.assertEqual((c.domain, c.event_type), (Domain.HUM, "epidemic"))
        c = classify("هجوم مسلح في مالي", "")
        self.assertEqual((c.domain, c.event_type), (Domain.SEC, "armed_attack"))

    def test_coup_is_maximum_impact(self):
        c = classify("Tentative de coup d'État à la présidence", "")
        self.assertEqual((c.domain, c.event_type, c.impact), (Domain.SEC, "coup_attempt", 5))

    def test_governance_and_economy(self):
        self.assertEqual(classify("Le scrutin présidentiel est reporté", "").domain, Domain.GOV)
        self.assertEqual(classify("Dévaluation de la monnaie nationale", "").event_type, "default_devaluation")

    def test_weak_or_absent_evidence_is_not_guessed(self):
        self.assertIsNone(classify("Inauguration d'un stade", "Une cérémonie a eu lieu."))
        self.assertIsNone(classify("Rapport annuel", "Le texte évoque une attaque une seule fois."))

    def test_cyber_words_do_not_match_inside_other_words(self):
        self.assertIsNone(classify("Hackathon étudiant", ""))

    def test_secondary_domain_is_reported(self):
        c = classify("Manifestation et émeute après le scrutin", "Les électeurs contestent l'élection.")
        self.assertIn(c.domain, (Domain.SOC, Domain.GOV))
        self.assertTrue(c.secondary_domains)


    def test_military_deployment_is_detected(self):
        titles = (
            "Troops deployed near the Niger border",
            "Military deployment in northern Niger",
            "Troop movements reported in southern Libya",
            "Military forces mobilize near the border",
            "Deploiement de troupes a la frontiere",
        )
        for title in titles:
            with self.subTest(title=title):
                result = classify(title, "")
                self.assertIsNotNone(result)
                self.assertEqual(
                    (result.domain, result.event_type),
                    (Domain.SEC, "military_deployment"),
                )

    def test_military_exercises_are_not_deployments(self):
        titles = (
            "Military exercise includes troop deployment near border",
            "Exercice militaire conjoint entre deux pays",
        )
        for title in titles:
            with self.subTest(title=title):
                self.assertIsNone(classify(title, ""))

    def test_major_fire_is_detected(self):
        cases = (
            ("Fire destroys 43 shops in Bamako market", ""),
            (
                "Incendie majeur au marche de Bamako",
                "Un incendie a detruit 43 boutiques du marche.",
            ),
        )
        for title, body in cases:
            with self.subTest(title=title):
                result = classify(title, body)
                self.assertIsNotNone(result)
                self.assertEqual(
                    (result.domain, result.event_type),
                    (Domain.HUM, "major_fire"),
                )

    def test_cross_border_expulsion_is_detected(self):
        cases = (
            ("Malians expelled from Guinea", ""),
            ("Guinea expels Malian miners after border operation", ""),
            (
                "Guinean authorities expelled Malian miners",
                "Authorities expelled Malian miners from the country.",
            ),
        )
        for title, body in cases:
            with self.subTest(title=title):
                result = classify(title, body)
                self.assertIsNotNone(result)
                self.assertEqual(
                    (result.domain, result.event_type),
                    (Domain.SOC, "cross_border_expulsion"),
                )

    def test_unrelated_events_are_not_misclassified(self):
        self.assertIsNone(classify("A market opens after renovation", ""))
        self.assertIsNone(classify("An employee expelled from a building", ""))



if __name__ == "__main__":
    unittest.main()
