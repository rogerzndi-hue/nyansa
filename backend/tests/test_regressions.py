"""Cas tirés de la première collecte réelle (titres reformulés) : faux positifs et pays erronés."""

import unittest
from datetime import datetime, timezone

from nyansa.classify import NEUTRAL_IMPACT_CAP, classify
from nyansa.geo import extract_countries
from nyansa.models import RawDocument, Source
from nyansa.pipeline import process_document
from nyansa.store import InMemoryStore


class DemonymTest(unittest.TestCase):
    def test_demonym_in_title_beats_country_in_body(self):
        e = extract_countries("Kenyans near the border fear Ebola after first death", "The case was reported close to Uganda.")
        self.assertEqual(e.primary, "KEN")
        self.assertIn("UGA", [h.iso3 for h in e.hits])

    def test_country_name_still_beats_demonym(self):
        self.assertEqual(extract_countries("Nigerian army raids camp in Mali").primary, "MLI")

    def test_nigerien_and_nigerian_are_distinct(self):
        self.assertEqual(extract_countries("Des Nigériens manifestent à Niamey").primary, "NER")
        self.assertEqual(extract_countries("Nigerians protest in Abuja").primary, "NGA")

    def test_sudanese_vs_south_sudanese(self):
        self.assertEqual(extract_countries("South Sudanese refugees arrive").primary, "SSD")
        self.assertEqual(extract_countries("Sudanese army advances").primary, "SDN")

    def test_congolese_is_ambiguous_not_guessed(self):
        e = extract_countries("Congolese leaders meet")
        self.assertIsNone(e.primary)

    def test_aes_alone_gives_no_country(self):
        self.assertIsNone(extract_countries("Espace AES : harmonisation de l'enseignement supérieur").primary)


class NeutralContextTest(unittest.TestCase):
    def test_training_and_project_titles_are_capped(self):
        for title, body in [
            ("Burkina Faso : 52 experts formés à la riposte contre les épidémies", ""),
            ("Un projet de bio-insecticide contre le paludisme et la dengue", "Paludisme, dengue."),
        ]:
            c = classify(title, body)
            self.assertIsNotNone(c, title)
            self.assertLessEqual(c.impact, NEUTRAL_IMPACT_CAP, title)
            self.assertTrue(any("contexte non incident" in x for x in c.evidence))

    def test_real_outbreak_is_not_capped(self):
        c = classify("Ebola outbreak passes 9,000 cases", "Ebola epidemic deaths.")
        self.assertEqual(c.impact, 4)

    def test_security_is_never_capped(self):
        c = classify("Meeting disrupted by terrorist attack", "Attaque terroriste, attentat.")
        self.assertGreaterEqual(c.impact, 4)

    def test_former_is_not_a_cue(self):
        c = classify("Former minister dies of cholera outbreak", "Cholera epidemic.")
        self.assertEqual(c.impact, 4)

    def test_end_to_end_criticality_drops(self):
        store = InMemoryStore()
        store.add_source(Source("s", "S", "rss", "C"))
        now = datetime.now(timezone.utc)
        process_document(store, RawDocument("s", "Burkina Faso : 52 experts formés à la riposte contre les épidémies", "", now, published_at=now))
        (event,) = store.events()
        self.assertLessEqual(event.criticality, 6)


if __name__ == "__main__":
    unittest.main()


class FalseKeywordTest(unittest.TestCase):
    def test_surtout_is_not_an_outbreak(self):
        self.assertIsNone(classify("Une tribune sur les langues", "Il faut surtout écrire correctement ; surtout, respecter la norme."))

    def test_portuguese_outbreak_still_matches(self):
        c = classify("Surto de cólera em Maputo", "Surto de cólera, epidemia.")
        self.assertEqual(c.event_type, "epidemic")

    def test_protestant_is_not_a_protest(self):
        self.assertIsNone(classify("L'Église protestante inaugure un temple", "La communauté protestante se réunit."))

    def test_protest_still_matches(self):
        c = classify("Protesters march in Nairobi", "Protests, demonstrators.")
        self.assertEqual(c.event_type, "protest")


class ArticleLinkTest(unittest.TestCase):
    def _store_with(self, url):
        from nyansa import services

        store = InMemoryStore()
        store.add_source(Source("s", "S", "rss", "C"))
        now = datetime.now(timezone.utc)
        process_document(store, RawDocument("s", "Attaque terroriste à Bamako, au Mali", "Attentat, terroristes.", now, url=url, published_at=now))
        return services.event_to_dict(store.events()[0], store)

    def test_event_exposes_article_url(self):
        view = self._store_with("https://exemple.test/article/1")
        self.assertEqual(view["sources"][0]["url"], "https://exemple.test/article/1")
        self.assertEqual(view["sources"][0]["source_id"], "s")

    def test_dangerous_schemes_are_dropped(self):
        for bad in ("javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "//evil.test/x", ""):
            self.assertIsNone(self._store_with(bad)["sources"][0]["url"], bad)


class VulnerabilityWordTest(unittest.TestCase):
    """Régression : « situation de vulnérabilité » (aide sociale) était classé Cyber."""

    def test_social_vulnerability_is_not_cyber(self):
        c = classify("Octroi de bourses à dix étudiants", "Des étudiants en situation de vulnérabilité reçoivent une aide. Vulnérabilité sociale.")
        self.assertIsNone(c)

    def test_software_vulnerability_is_still_cyber(self):
        from nyansa.taxonomy import Domain

        c = classify("Vulnérabilité critique corrigée dans un logiciel bancaire", "Une vulnérabilité informatique (CVE-2026-1234) a été publiée.")
        self.assertIsNotNone(c)
        self.assertEqual(c.domain, Domain.CYB)


class NewPhrasesTest(unittest.TestCase):
    """Formulations vues dans la première collecte sociale réelle et restées « thème non reconnu »."""

    def test_internet_disruption(self):
        c = classify("Internet disruption detected in Kadiogo, Burkina Faso — Severe Disruption (source: IODA)")
        self.assertIsNotNone(c)
        self.assertEqual(c.event_type, "internet_shutdown")

    def test_defence_cooperation_letter(self):
        from nyansa.taxonomy import Domain

        c = classify("La France et le Tchad signent une lettre d'intention pour rétablir leur coopération en matière de défense")
        self.assertIsNotNone(c)
        self.assertEqual(c.domain, Domain.GEO)


class ForeignFocusTest(unittest.TestCase):
    """Régression : « Bangladesh battles deadly measles outbreak » est apparu dans l'alerte flash Nigeria (niveau Élevé)."""

    def doc(self, store, title, text, url, source="s"):
        return process_document(store, RawDocument(source_id=source, title=title, text=text, url=url, published_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
                                                   fetched_at=datetime(2026, 10, 18, tzinfo=timezone.utc)))

    def store(self):
        st = InMemoryStore()
        st.add_source(Source("s", "S", "rss", "C"))
        return st

    def test_foreign_title_with_single_african_mention_is_not_routed(self):
        out = self.doc(self.store(), "Bangladesh battles deadly measles outbreak as investigation highlights vaccination gaps",
                       "Measles cases rise in Bangladesh. Health officials in Nigeria also watch the outbreak.", "https://x.test/1")
        self.assertEqual((out.status, out.reason), ("unrouted", "foreign_focus"))

    def test_foreign_title_with_strong_african_body_is_still_routed(self):
        out = self.doc(self.store(), "China lends two billion dollars for new railway",
                       "The railway links Nairobi to the coast. Kenya will repay the debt over 20 years. Kenya's treasury says debt is sustainable; Kenya expects growth.", "https://x.test/2")
        self.assertEqual(out.status, "routed")

    def test_african_country_in_title_always_wins(self):
        out = self.doc(self.store(), "France et Mali : attaque terroriste signalée à Bamako", "Une attaque terroriste a fait des victimes.", "https://x.test/3")
        self.assertEqual(out.status, "routed")


class SocialDoesNotRaiseCredibilityTest(unittest.TestCase):
    """Régression : un même message relayé par #Mali et #BurkinaFaso donnait « E2 » (probablement vrai)."""

    def build(self, *channels, publisher_ids=None):
        st = InMemoryStore()
        for i, ch in enumerate(channels):
            publisher_id = publisher_ids[i] if publisher_ids is not None else None
            st.add_source(Source(
                f"s{i}", f"S{i}", "rss", "E" if ch == "social" else "B",
                channel=ch, publisher_id=publisher_id,
            ))
            process_document(st, RawDocument(source_id=f"s{i}", title="Attaque terroriste à Bamako, au Mali", text="Une attaque terroriste a eu lieu à Bamako, Mali.",
                                             url=f"https://x.test/{i}", published_at=datetime(2026, 10, 18, tzinfo=timezone.utc), fetched_at=datetime(2026, 10, 18, tzinfo=timezone.utc)))
        (event,) = st.events()
        return event

    def test_two_social_sources_stay_at_credibility_3(self):
        e = self.build("social", "social")
        self.assertEqual((e.info_credibility, e.admiralty), (3, "E3"))

    def test_social_plus_one_media_stays_at_3(self):
        self.assertEqual(self.build("media", "social").info_credibility, 3)

    def test_two_media_sources_raise_to_2(self):
        self.assertEqual(self.build("media", "media").info_credibility, 2)

    def test_two_feeds_from_same_publisher_do_not_raise_credibility(self):
        event = self.build(
            "media", "media",
            publisher_ids=("beninwebtv", "beninwebtv"),
        )
        self.assertEqual((event.source_count, event.info_credibility), (2, 3))

    def test_two_independent_publishers_raise_credibility(self):
        event = self.build(
            "media", "media",
            publisher_ids=("beninwebtv", "independent-media"),
        )
        self.assertEqual((event.source_count, event.info_credibility), (2, 2))


class SameLinkSameSourceNumberTest(unittest.TestCase):
    """Régression : le même message reçu par deux flux figurait deux fois ([6][7]) dans la liste des sources d'un livrable."""

    def test_same_url_gets_one_number(self):
        from nyansa.deliverables.builder import _Sources

        st = InMemoryStore()
        for sid in ("a", "b"):
            st.add_source(Source(sid, sid.upper(), "rss", "E", channel="social"))
            process_document(st, RawDocument(source_id=sid, title="Attaque terroriste à Bamako, au Mali", text="Une attaque terroriste a eu lieu à Bamako, Mali.",
                                             url="https://mastodon.social/@x/1", published_at=datetime(2026, 10, 18, tzinfo=timezone.utc),
                                             fetched_at=datetime(2026, 10, 18, tzinfo=timezone.utc)))
        (event,) = st.events()
        src = _Sources(st, "fr")
        self.assertEqual(src.refs(event), [1])
        self.assertEqual(len(src.items), 1)


class SocialSecurityPhrasesTest(unittest.TestCase):
    """Formulations de la collecte sociale réelle restées « thème non reconnu » (liste des articles à qualifier du 20 octobre 2026)."""

    CASES = [
        ("Clash between Africa Corps and JNIM militants in Mali", "armed_clash"),
        ("Armed Men Kill Over 200 People In Burkina Faso", "armed_clash"),
        ("Russian Lancet strikes JNIM vehicle in Mali", "armed_clash"),
        ("Battle for Mali base has killed dozens of militants, army says", "armed_clash"),
        ("Katsina Health Workers Suspend Strike", "strike"),
        ("Web censorship anomalies in Nigeria (source: OONI)", "internet_shutdown"),
        ("Deutsche Bank adds Nigeria to its frontier foreign exchange monitor, naira undervalued", "macro_economy"),
    ]

    def test_each_phrase_is_classified(self):
        for title, event_type in self.CASES:
            with self.subTest(title=title):
                c = classify(title)
                self.assertIsNotNone(c)
                self.assertEqual(c.event_type, event_type)

    def test_metaphors_and_music_stay_unclassified(self):
        for title in ("Bangladesh battles deadly measles outbreak", "Government and opposition clash over the budget",
                      "Nigeria Afrobeat Special: compilation", "Hilarious quips and energy: the hypemen redefining Afrobeats music"):
            with self.subTest(title=title):
                c = classify(title)
                self.assertTrue(c is None or c.event_type not in ("armed_clash",), title)


class UnroutedPerSourceTest(unittest.TestCase):
    def test_endpoint_lists_up_to_ten_per_source(self):
        import http.client, json, threading
        from http.server import ThreadingHTTPServer
        from nyansa.devserver import make_handler

        st = InMemoryStore()
        for sid in ("a", "b"):
            st.add_source(Source(sid, sid, "rss", "C"))
        for i in range(2):  # b : 2 articles sans pays/thème, plus anciens ; a : 30 articles, plus récents
            process_document(st, RawDocument(source_id="b", title=f"Texte sans objet numéro {i}", text="rien", url=None, fetched_at=datetime(2026, 10, 18, tzinfo=timezone.utc)))
        for i in range(30):
            process_document(st, RawDocument(source_id="a", title=f"Autre texte sans objet numéro {i}", text="rien", url=None, fetched_at=datetime(2026, 10, 19, tzinfo=timezone.utc)))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(st, demo=False, live=None))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            c = http.client.HTTPConnection("127.0.0.1", srv.server_port)
            c.request("GET", "/api/unrouted")
            items = json.loads(c.getresponse().read())
            counts = {}
            for it in items:
                counts[it["source_id"]] = counts.get(it["source_id"], 0) + 1
            self.assertEqual(counts, {"a": 10, "b": 2})  # b n'est plus noyée par a
        finally:
            srv.shutdown()
            srv.server_close()


class France24PhrasesTest(unittest.TestCase):
    """Articles France 24 – Afrique restés « sans thème reconnu » (liste du 20 octobre 2026)."""

    CASES = [
        ("Tchad : des affrontements autour des ressources aurifères du Tibesti", "armed_clash", "TCD"),
        ("Bientôt 500 jours de détention pour Christophe Gleizes en Algérie", "human_rights", "DZA"),
        ("Belgique : perquisitions chez des proches du président de la RDC Félix Tshisekedi", "judicial_action", "COD"),
        ("Alliance Kabila - AFC/M23 : la RDC sous pression", "armed_clash", "COD"),
        ("TOP STORY IN NIGERIA: RIGHTS GROUPS ALLEGE TORTURE, SECRET DETENTIONS IN NIGERIA'S SOUTHEAST", "human_rights", "NGA"),
    ]

    def test_each_headline_is_classified_and_routed_to_the_right_country(self):
        for title, event_type, iso in self.CASES:
            with self.subTest(title=title):
                c = classify(title)
                self.assertIsNotNone(c)
                self.assertEqual(c.event_type, event_type)
                self.assertEqual(extract_countries(title).primary, iso)

    def test_non_risk_stories_stay_unclassified(self):
        for title in ("Gambie : Le cancer du col de l'utérus, un enjeu majeur de santé publique",
                      "Côte d'Ivoire : un premier championnat amateur africain de MMA à Abidjan",
                      "Maladies rénales : pourquoi l'Afrique manque de moyens pour soigner ses patients",
                      "Tradition festive ou \"crime\" : pourquoi les \"bouquets d'argent\" font-ils polémique en Afrique ?",
                      "Les actifs détenus par l'État progressent"):
            with self.subTest(title=title):
                self.assertIsNone(classify(title))


class DomesticPoliticsTest(unittest.TestCase):
    """Décision du 20 octobre 2026 : la politique intérieure entre dans les événements (gouvernance, impact 2)."""

    def test_headlines_are_classified_as_governance(self):
        from nyansa.taxonomy import Domain

        for title in ("RD Congo : la majorité réagit à la création du front de Joseph Kabila",
                      "Nigeria: opposition leaders reject the ruling party's proposal",
                      "Sénégal : remaniement ministériel et crise politique à Dakar",
                      "Kenya: MPs file a no-confidence motion against the cabinet"):
            with self.subTest(title=title):
                c = classify(title)
                self.assertIsNotNone(c)
                self.assertEqual((c.domain, c.event_type), (Domain.GOV, "political_tension"))
                self.assertLessEqual(c.impact, 2)

    def test_generic_wording_is_not_political_tension(self):
        for title in ("La majorité des Africains utilisent le mobile", "A coalition led by the United States visits the region",
                      "Opposition to the road project grows"):
            with self.subTest(title=title):
                c = classify(title)
                if title.startswith("Opposition"):
                    self.assertIsNotNone(c)  # « opposition » en titre : accepté, signal faible (impact 2)
                else:
                    self.assertIsNone(c)

    def test_body_only_mention_is_below_threshold(self):
        self.assertIsNone(classify("Rencontre à Dakar", "Le parti au pouvoir a parlé."))


class France24Round3Test(unittest.TestCase):
    def test_new_headlines(self):
        cases = {"Plus de 3 000 personnes à Tunis contre la vie chère et pour les libertés publiques": "protest",
                 "\"Une priorité absolue\" de ramener les pays du Sahel dans l'OIF, souligne la Mauritanienne Coumba Bâ": "diplomacy"}
        for title, expected in cases.items():
            with self.subTest(title=title):
                c = classify(title)
                self.assertIsNotNone(c)
                self.assertEqual(c.event_type, expected)

    def test_chronic_health_and_culture_stay_unclassified(self):
        for title in ("Cancer du sein au Sénégal : le coût des mammographies, un frein au dépistage",
                      "Côte d’Ivoire : un premier championnat amateur africain de MMA à Abidjan"):
            with self.subTest(title=title):
                self.assertIsNone(classify(title))


class SameStoryClusteringTest(unittest.TestCase):
    def test_cross_language_and_rewording_merge(self):
        from nyansa.store import _same_story, _title_tokens, _country_words
        ig = _country_words("KEN")
        pairs = [("Kenya : un huitième cas du virus Ebola a été recensé", "Kenya confirms eighth Ebola case as regional outbreak spreads"),
                 ("Kenyan cult leader Paul Mackenzie faces verdict over 191 starvation deaths",
                  "Guilty as charged: Kenyan court finds cult leader responsible of 191 starvation deaths")]
        for a, b in pairs:
            with self.subTest(a=a):
                self.assertTrue(_same_story(_title_tokens(a), _title_tokens(b), ig))

    def test_different_stories_do_not_merge(self):
        from nyansa.store import _same_story, _title_tokens, _country_words
        ig = _country_words("NGA")
        self.assertFalse(_same_story(_title_tokens("Nigeria: police arrest suspects in Lagos kidnapping"),
                                     _title_tokens("Nigeria: police fire tear gas at Abuja protesters"), ig))

    def test_cult_trial_is_not_famine(self):
        c = classify("Kenyan cult leader Paul Mackenzie faces verdict over 191 starvation deaths")
        self.assertTrue(c is None or c.event_type != "famine")  # procès criminel : jamais une famine, plus un risque de gouvernance

    def test_tigray_interim_government(self):
        c = classify("Ethiopia appoints interim Tigray government after TPLF ouster")
        self.assertEqual(c.event_type, "political_tension")


class ClusteringVariantsTest(unittest.TestCase):
    def _tokens_same(self, a, b, iso):
        from nyansa.store import _country_words, _same_story, _title_tokens
        return _same_story(_title_tokens(a), _title_tokens(b), _country_words(iso))

    def test_same_number_merges(self):
        self.assertTrue(self._tokens_same("Guilty as charged: Kenyan court finds cult leader responsible of 191 starvation deaths",
                                          "Kenyan cult leader Paul Mackenzie faces verdict over 191 starvation deaths", "KEN"))

    def test_abuse_synonyms_merge(self):
        self.assertTrue(self._tokens_same("Mali: Human Rights Watch Denounces Crimes Against Civilians [Africanews Today]",
                                          "Mali: Malian Army and Rebels Committing Abuses Against Civilians, Rights Group Says", "MLI"))

    def test_different_events_stay_apart(self):
        self.assertFalse(self._tokens_same("Kenya: 191 pupils sit exam", "Kenya: flood kills 12 near Nairobi", "KEN"))


class ClusteringRound2Test(unittest.TestCase):
    def _same(self, a, b, iso):
        from nyansa.store import _country_words, _same_story, _title_tokens
        return _same_story(_title_tokens(a), _title_tokens(b), _country_words(iso))

    def test_fr_en_same_fact(self):
        self.assertTrue(self._same("More than 40 civilians killed in Mali attack blamed on Islamic State",
                                   "Mali : plus de 40 civils tués dans des attaques armées dans le Nord-Est", "MLI"))

    def test_cult_verdict_variants(self):
        self.assertTrue(self._same("Kenya: Relatives welcome guilty verdict in Shakahola starvation cult massacre",
                                   "Guilty as charged: Kenyan court finds cult leader responsible of 191 starvation deaths", "KEN"))

    def test_distinct_facts_stay_apart(self):
        self.assertFalse(self._same("US raises Kenya travel advisory over Ebola and security concerns",
                                    "Kenya confirms eighth Ebola case as regional outbreak spreads", "KEN"))

    def test_sec_and_gov_events_can_merge(self):
        from nyansa.store import _same_domain
        from nyansa.taxonomy import Domain
        self.assertTrue(_same_domain(Domain.SEC, Domain.GOV))
        self.assertFalse(_same_domain(Domain.SEC, Domain.ECO))


class OrdinaryTrialTest(unittest.TestCase):
    def test_criminal_trial_is_not_governance(self):
        for t in ("Kenya: Relatives welcome guilty verdict in Shakahola starvation cult massacre",
                  "Kenya cult deaths: Court set to rule in preacher's murder trial"):
            with self.subTest(t=t):
                c = classify(t)
                self.assertTrue(c is None or c.event_type != "judicial_action")

    def test_official_targeted_action_remains(self):
        c = classify("Nigeria: opposition leader sentenced to five years")
        self.assertIsNotNone(c)
        self.assertEqual(c.domain.name, "GOV")


class TrialBodyViolenceTest(unittest.TestCase):
    def test_trial_headline_with_violent_body_is_not_security(self):
        c = classify("Kenya cult deaths: Court set to rule in preacher's murder trial",
                     "Prosecutors say the cult members were killed in a massacre; the murder of 191 people shocked the country.")
        self.assertTrue(c is None or c.domain.name != "SEC")

    def test_attack_headline_with_court_in_body_stays_security(self):
        c = classify("Gunmen kill 12 villagers in Zamfara", "A court had previously ordered protection for the village.")
        self.assertIsNotNone(c)
        self.assertEqual(c.domain.name, "SEC")


class CasualtyFiguresTest(unittest.TestCase):
    def test_figures_extracted_in_fr_and_en(self):
        from nyansa.deliverables.builder import casualty_figures
        self.assertEqual(casualty_figures(["More than 40 civilians killed in Mali attack", "Mali : plus de 48 morts dans des attaques",
                                           "Gunmen killed at least 12 villagers"]), [12, 40, 48])

    def test_no_figure_no_note(self):
        from nyansa.deliverables.builder import casualty_figures
        self.assertEqual(casualty_figures(["Kenya confirms eighth Ebola case", "Mali talks resume"]), [])

    def test_thousands_separator(self):
        from nyansa.deliverables.builder import casualty_figures
        self.assertEqual(casualty_figures(["Floods: 1,200 dead in Sudan"]), [1200])


class MaliDraftRound3Test(unittest.TestCase):
    def test_figure_with_people_have_been_killed(self):
        from nyansa.deliverables.builder import casualty_figures
        self.assertEqual(casualty_figures(["At least 48 people have been killed in attacks attributed to Islamic State-linked militants"]), [48])

    def test_hrw_report_titles_merge(self):
        from nyansa.store import _country_words, _same_story, _title_tokens
        self.assertTrue(_same_story(_title_tokens("Mali: Human Rights Watch Denounces Crimes Against Civilians [Africanews Today]"),
                                    _title_tokens("Mali's army and rebel opponents guilty of human rights violations, new report finds"),
                                    _country_words("MLI")))


class MultilingualCoverageTest(unittest.TestCase):
    def test_es_de_it_headlines(self):
        cases = {
            "Los enfrentamientos mortales se intensifican entre yihadistas en el Sahel": "SEC",
            "Mali-Konflikt: Dschihadisten greifen Armeeposten an": "SEC",
            "Mali: attacco di jihadisti contro una base militare": "SEC",
            "Protestas en Nairobi: miles de manifestantes exigen reformas": "SOC",
            "Proteste in Kenia: Demonstranten fordern Reformen": "SOC",
            "Kenya: sciopero dei medici nel Paese": "SOC",
            "Niger: colpo di stato militare a Niamey": "SEC",
        }
        for title, domain in cases.items():
            with self.subTest(title=title):
                c = classify(title)
                self.assertIsNotNone(c)
                self.assertEqual(c.domain.name, domain)

    def test_non_events_stay_unclassified(self):
        for title in ("Toumani Diabaté's Symmetric Orchestra", "Alle frontiere del tempo", "Legerleider Burkina Faso grijpt macht over voetbalbond"):
            with self.subTest(title=title):
                self.assertIsNone(classify(title))


class SpanishSameFactTest(unittest.TestCase):
    def test_spanish_title_joins_english_event(self):
        from nyansa.store import _country_words, _same_story, _title_tokens
        self.assertTrue(_same_story(_title_tokens("Ataque terrorista del Estado Islámico deja 48 civiles muertos en Malí…"),
                                    _title_tokens("More than 40 civilians killed in Mali attack blamed on Islamic State…"), _country_words("MLI")))
