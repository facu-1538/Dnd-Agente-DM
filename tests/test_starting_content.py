import unittest
from unittest.mock import patch

from rag.fixed_content import class_documents, starting_documents, classes_from_query, profiles
from game_engine.character_creation import default_choices, build_character


class StartingContentTests(unittest.TestCase):
    def test_all_four_classes_have_fourteen_chunks_without_vector_queries(self):
        with patch("rag.retriever.get_retriever", side_effect=AssertionError("No usar FAISS")), patch("agent.tools._log"):
            from agent.tools import get_starting_rules, query_rules_rag
            docs = class_documents()
            self.assertEqual(len(docs), 14)
            self.assertEqual({d.metadata["titulo"] for d in docs}, {"Fighter", "Wizard", "Rogue", "Cleric"})
            result = get_starting_rules.invoke({"need": "comparar_clases"})
            self.assertGreater(result.count("[Fuente "), 14)
            result = query_rules_rag.invoke({"query": "comparar clases", "category": "classes"})
            self.assertEqual(result.count("[Fuente "), 14)

    def test_selected_class_does_not_include_other_classes(self):
        docs = starting_documents("crear_personaje", "mago")
        raw = [d for d in docs if d.metadata.get("categoria") == "classes"]
        self.assertEqual(len(raw), 4)
        self.assertEqual({d.metadata["titulo"] for d in raw}, {"Wizard"})
        self.assertEqual({d.metadata["titulo"] for d in classes_from_query("rasgos del guerrero")}, {"Fighter"})
        self.assertEqual({d.metadata["titulo"] for d in classes_from_query("rasgos del picaro")}, {"Rogue"})

    def test_magic_packets_include_only_spells_in_selected_class_menu(self):
        for key, profile in profiles().items():
            with self.subTest(class_id=key):
                titles = {d.metadata["titulo"] for d in starting_documents("magia_inicial", key)
                          if d.metadata.get("categoria") == "spells"}
                expected = set(profile["cantrips"] + profile["spells"] + profile.get("domain_spells", []))
                self.assertEqual(titles, expected)

    def test_invalid_filters_fail_explicitly(self):
        with self.assertRaises(ValueError):
            starting_documents("no_existe")
        with self.assertRaises(ValueError):
            starting_documents("crear_personaje", "elfo")


class CharacterCreationTests(unittest.TestCase):
    def test_four_defaults_produce_complete_level_one_characters(self):
        for key in profiles():
            with self.subTest(class_id=key):
                result = build_character(dict(default_choices(key), name="Arin"))
                self.assertEqual(sorted(result["atributos"].values()), [8, 10, 12, 13, 14, 15])
                self.assertEqual(result["hp"], 100)
                self.assertEqual(result["oro"], 50)
                self.assertEqual(result["raza"], "")
                self.assertEqual(result["nivel"], 1)
                self.assertTrue(result["ficha_creada"])
                self.assertTrue(result["equipo"])
                self.assertTrue(result["ataques"])

    def test_fighter_defense_and_dueling_change_only_applicable_values(self):
        choices = dict(default_choices("fighter"), name="Arin")
        defense = build_character(choices)
        dueling = build_character(dict(choices, style="Duelo"))
        self.assertEqual((defense["ca"], dueling["ca"]), (19, 18))
        self.assertEqual((defense["ataques"][0]["danio"], dueling["ataques"][0]["danio"]), ("1d8+2", "1d8+4"))
        with self.assertRaises(ValueError):
            build_character(dict(choices, gear="duelist", style="Duelo"))

    def test_wizard_cannot_prepare_spell_outside_book(self):
        choices = dict(default_choices("wizard"), name="Arin")
        with self.assertRaises(ValueError):
            build_character(dict(choices, prepared=["Sleep", "Magic Missile", "Shield"]))
        result = build_character(choices)
        self.assertEqual(result["magia"]["cd"], 12)
        self.assertEqual(result["magia"]["ataque"], 4)
        self.assertEqual(result["magia"]["espacios"]["1"]["restantes"], 2)

    def test_cleric_domain_spells_do_not_replace_prepared_choices(self):
        result = build_character(dict(default_choices("cleric"), name="Arin"))
        self.assertEqual(len(result["magia"]["preparados"]), 3)
        self.assertEqual(result["magia"]["dominio"], ["Bless", "Cure Wounds"])
        self.assertEqual(result["subclase"], "Dominio de la Vida")

    def test_rogue_expertise_requires_selected_skills(self):
        choices = dict(default_choices("rogue"), name="Arin")
        result = build_character(choices)
        self.assertEqual(result["habilidades"]["Sigilo"], 6)
        self.assertEqual(result["habilidades"]["Percepción"], 3)
        with self.assertRaises(ValueError):
            build_character(dict(choices, expertise=["Engaño", "Sigilo"]))

    def test_invalid_or_repeated_choices_cannot_be_confirmed(self):
        base = dict(default_choices("fighter"), name="Arin")
        for invalid in ({"name": " "}, {"name": "a" * 61}, {"gear": "plate"},
                        {"skills": ["Atletismo", "Atletismo"]}, {"skills": ["Arcanos", "Atletismo"]}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                build_character(dict(base, **invalid))
