import unittest
from unittest.mock import Mock, patch
import requests

from rag.retriever import RAGRetriever


def spell(name, level, school="Evocation", source="wotc-srd"):
    return {"name": name, "level": f"{level}rd-level", "school": school,
            "desc": "Un efecto de prueba", "document": {"slug": source}}


class FallbackTests(unittest.TestCase):
    def test_api_keeps_filters_and_rejects_other_sources(self):
        response = Mock()
        response.json.return_value = {"results": [
            spell("Nivel incorrecto", 1), spell("Escuela incorrecta", 3, "Illusion"),
            spell("Otro reglamento", 3, source="srd-2024"), spell("Fireball", 3),
        ]}
        with patch("rag.retriever.requests.get", return_value=response) as get:
            result = RAGRetriever().search_api("fire", category="spells", spell_level=3, school="evocación")
        self.assertIn("Fireball", result)
        self.assertNotIn("incorrect", result)
        self.assertNotIn("Otro reglamento", result)
        self.assertEqual(get.call_args.kwargs["params"]["document__slug"], "wotc-srd")
        self.assertEqual(get.call_args.kwargs["timeout"], 10)

    def test_api_monster_filters_use_type_and_cr(self):
        response = Mock()
        response.json.return_value = {"results": [
            {"name": "Zombie", "type": "undead", "challenge_rating": "1/4", "document": {"slug": "wotc-srd"}},
            {"name": "Goblin", "type": "humanoid", "challenge_rating": "1/4", "document": {"slug": "wotc-srd"}},
            {"name": "Lich", "type": "undead", "challenge_rating": "21", "document": {"slug": "wotc-srd"}},
        ]}
        with patch("rag.retriever.requests.get", return_value=response):
            result = RAGRetriever().search_api("creatures", category="monsters", monster_type="no muerto", max_cr=2)
        self.assertIn("Zombie", result)
        self.assertNotIn("Goblin", result)
        self.assertNotIn("Lich", result)

    def test_legacy_api_provenance_and_exact_title_are_supported(self):
        response = Mock()
        item = spell("Fireball", 3)
        item.pop("document")
        item["document__slug"] = "wotc-srd"
        response.json.return_value = {"results": [spell("Delayed Blast Fireball", 7), item]}
        with patch("rag.retriever.requests.get", return_value=response):
            result = RAGRetriever().search_api("Fireball", category="spells", k=1)
        self.assertIn("## Fireball", result)
        self.assertNotIn("Delayed Blast", result)

    def test_network_failure_returns_none(self):
        with patch("rag.retriever.requests.get", side_effect=requests.Timeout):
            self.assertIsNone(RAGRetriever().search_api("fire", category="spells"))

    def test_tool_forwards_every_filter_when_local_search_is_unavailable(self):
        from agent.tools import query_rules_rag
        retriever = Mock()
        retriever.search_filtered.side_effect = ValueError("Sin clave para embeddings")
        retriever.search_api.return_value = "Respuesta de respaldo"
        with patch("rag.retriever.get_retriever", return_value=retriever), patch("agent.tools._log"):
            result = query_rules_rag.invoke({"query": "fire", "category": "spells", "spell_level": 3,
                                            "school": "evocation"})
        self.assertEqual(result, "Respuesta de respaldo")
        retriever.search_api.assert_called_once_with("fire", category="spells", spell_level=3,
                                                   school="evocation", monster_type=None, max_cr=None)

    def test_relevance_threshold_discards_distant_neighbors(self):
        from test_retriever import make_retriever
        retriever = make_retriever([[2.0, 0.0]], [{"categoria": "spells"}])
        result = retriever.search_filtered("tema desconocido", category="spells")
        self.assertTrue(result.startswith("Sin resultados"))

    def test_main_threshold_accepts_near_neighbors_but_rejects_distance_point75(self):
        from test_retriever import make_retriever
        for coordinate, accepted in [(0.8, True), (0.8660254, False)]:
            with self.subTest(squared_distance=coordinate ** 2):
                retriever = make_retriever([[coordinate, 0.0]], [{"categoria": "spells"}])
                result = retriever.search_filtered("tema desconocido", category="spells")
                self.assertEqual("[Fuente " in result, accepted)
