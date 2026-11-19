"""Pruebas del RAG con FAISS real y embeddings locales; no requieren API key."""
from pathlib import Path
import unittest
from unittest.mock import patch

import faiss
import numpy as np
from langchain_core.embeddings import Embeddings
from langchain_community.vectorstores import FAISS

from rag.retriever import RAGRetriever


class LocalEmbeddings(Embeddings):
    def __init__(self, vector):
        self.vector = list(vector)
        self.queries = []

    def embed_query(self, text):
        self.queries.append(text)
        return self.vector

    def embed_documents(self, texts):
        raise AssertionError("Las búsquedas no deben recalcular embeddings del corpus")


def make_retriever(vectors, metadata, **kwargs):
    embeddings = LocalEmbeddings([0.0] * len(vectors[0]))
    store = FAISS.from_embeddings(
        [(f"documento {i}", list(v)) for i, v in enumerate(vectors)],
        embeddings,
        metadatas=[{"titulo": f"Entrada {i}", "hash": str(i), **m}
                   for i, m in enumerate(metadata)],
        **kwargs,
    )
    retriever = RAGRetriever()
    retriever.vectorstore = store
    retriever.embeddings = embeddings
    retriever._cargado = True
    return retriever


class PrefilterTests(unittest.TestCase):
    def setUp(self):
        # Estas pruebas aíslan el prefiltrado del umbral de relevancia añadido upstream.
        threshold = patch("rag.retriever.UMBRAL_DISTANCIA", float("inf"))
        threshold.start()
        self.addCleanup(threshold.stop)

    def test_faiss_receives_only_matching_ids_even_beyond_first_twenty(self):
        # Los primeros 30 vecinos pertenecen a otra categoría.
        retriever = make_retriever(
            [[float(i), 0.0] for i in range(34)],
            [{"categoria": "monsters" if i < 30 else "armor"} for i in range(34)],
        )
        index = retriever.vectorstore.index
        with patch.object(index, "search", wraps=index.search) as search:
            result = retriever.search_filtered("protección", category="armor")
        for i in range(30, 34):
            self.assertIn(f"documento {i}", result)
        self.assertEqual(result.count("[Fuente "), 4)
        params = search.call_args.kwargs["params"]
        self.assertEqual([i for i in range(34) if params.sel.is_member(i)], list(range(30, 34)))
        self.assertEqual(search.call_args.args[1], 4)
        self.assertEqual(retriever.embeddings.queries, ["protección"])

    def test_no_candidates_skips_embedding_and_vector_search(self):
        retriever = make_retriever([[0.0, 0.0]], [{"categoria": "spells"}])
        with patch.object(retriever.vectorstore.index, "search") as search:
            result = retriever.search_filtered("dragones", category="monsters")
        self.assertTrue(result.startswith("Sin resultados con los filtros"))
        self.assertEqual(retriever.embeddings.queries, [])
        search.assert_not_called()

    def test_spell_filters_are_conjunctive_and_normalize_spanish(self):
        retriever = make_retriever(
            [[float(i), 0.0] for i in range(4)],
            [
                {"categoria": "spells", "nivel": 2, "escuela": "evocation"},
                {"categoria": "spells", "nivel": 3, "escuela": "conjuration"},
                {"categoria": "spells", "nivel": 3, "escuela": "evocation"},
                {"categoria": "monsters", "nivel": 3, "escuela": "evocation"},
            ],
        )
        result = retriever.search_filtered("fuego", category="spells", spell_level=3, school="Evocación")
        self.assertIn("documento 2", result)
        self.assertEqual(result.count("[Fuente "), 1)

    def test_monster_type_and_inclusive_max_cr(self):
        retriever = make_retriever(
            [[float(i), 0.0] for i in range(5)],
            [
                {"categoria": "monsters", "tipo": "undead", "cr": 3.0},
                {"categoria": "monsters", "tipo": "humanoid", "cr": 0.25},
                {"categoria": "monsters", "tipo": "undead", "cr": 2.0},
                {"categoria": "monsters", "tipo": "undead", "cr": 0.25},
                {"categoria": "monsters", "tipo": "undead"},
            ],
        )
        result = retriever.search_filtered("criaturas", category="monsters", monster_type="no muerto", max_cr=2)
        self.assertIn("documento 2", result)
        self.assertIn("documento 3", result)
        self.assertEqual(result.count("[Fuente "), 2)

    def test_zero_level_and_zero_cr_are_real_filters(self):
        retriever = make_retriever(
            [[float(i), 0.0] for i in range(4)],
            [
                {"categoria": "spells", "nivel": 1},
                {"categoria": "spells", "nivel": 0},
                {"categoria": "monsters", "cr": 0.25},
                {"categoria": "monsters", "cr": 0.0},
            ],
        )
        for filters, expected in [({"spell_level": 0}, 1), ({"max_cr": 0}, 3)]:
            with self.subTest(filters=filters):
                result = retriever.search_filtered("consulta", **filters)
                self.assertIn(f"documento {expected}", result)
                self.assertEqual(result.count("[Fuente "), 1)

    def test_incompatible_filters_do_not_fall_back_to_general(self):
        retriever = make_retriever(
            [[0.0, 0.0], [1.0, 0.0]],
            [{"categoria": "spells"}, {"categoria": "monsters", "tipo": "undead"}],
        )
        result = retriever.search_filtered("consulta", category="spells", monster_type="undead")
        self.assertTrue(result.startswith("Sin resultados con los filtros"))
        self.assertEqual(retriever.embeddings.queries, [])

    def test_title_priority_deduplication_limit_and_sources_are_preserved(self):
        retriever = make_retriever(
            [[0.0, 0.0], [1.0, 0.0], [8.0, 0.0]],
            [
                {"categoria": "spells", "titulo": "Spark"},
                {"categoria": "spells", "titulo": "Fireball"},
                {"categoria": "spells", "titulo": "Fireball"},
            ],
        )
        result = retriever.search_filtered("Fireball", category="spells", k=2)
        self.assertLess(result.index("documento 1"), result.index("documento 0"))
        self.assertEqual(result.count("documento 1"), 1)
        self.assertEqual(result.count("[Fuente "), 2)
        self.assertIn("FUENTES CONSULTADAS: spells.txt › Fireball; spells.txt › Spark", result)

    def test_unfiltered_queries_still_use_general_search(self):
        retriever = make_retriever(
            [[0.0, 0.0], [1.0, 0.0]],
            [{"categoria": "spells"}, {"categoria": "monsters"}],
        )
        store = retriever.vectorstore
        with patch.object(store, "similarity_search_with_score", wraps=store.similarity_search_with_score) as search:
            result = retriever.search_filtered("consulta")
        search.assert_called_once_with("consulta", k=4)
        self.assertEqual(result.count("[Fuente "), 2)

    def test_normalized_vectors_keep_the_same_ranking(self):
        retriever = make_retriever(
            [[10.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
            [{"categoria": "spells"}] * 3,
            normalize_L2=True,
        )
        retriever.embeddings.vector = [2.0, 1.0]
        filtro = retriever._crear_filtro("spells", None, None, None, None)
        expected = retriever.vectorstore.similarity_search("consulta", k=3, filter=filtro, fetch_k=3)
        actual = retriever._buscar_pre_filtrado("consulta", filtro, 3)
        self.assertEqual([d.page_content for d, _ in actual], [d.page_content for d in expected])


class BundledIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.embeddings = LocalEmbeddings([0.0] * 3072)
        path = Path(__file__).resolve().parents[1] / "storage" / "vector_db"
        cls.store = FAISS.load_local(str(path), cls.embeddings, allow_dangerous_deserialization=True)

    def test_final_retrieval_matches_previous_algorithm_on_real_index(self):
        cases = [
            ("Shield", {"category": "armor"}),
            ("Longsword", {"category": "weapons"}),
            ("Wizard", {"category": "classes"}),
            ("Grappled", {"category": "conditions"}),
            ("Attack Rolls", {"category": "rules"}),
            ("Fireball", {"category": "spells", "spell_level": 3, "school": "evocación"}),
            ("Goblin", {"category": "monsters", "monster_type": "humanoide", "max_cr": 0.25}),
            ("magia", {"spell_level": 0}),
            ("criaturas", {"category": "monsters", "max_cr": 0}),
            ("Cure Wounds", {}),
        ]
        # Vectores deterministas: uno del índice y otro independiente de su contenido.
        rng = np.random.default_rng(2026)
        vectors = [self.store.index.reconstruct(0), rng.standard_normal(3072).astype(np.float32)]
        for vector in vectors:
            self.embeddings.vector = vector.tolist()
            for query, filters in cases:
                with self.subTest(query=query, filters=filters, vector_norm=float(np.linalg.norm(vector))):
                    retriever = RAGRetriever()
                    retriever.vectorstore = self.store
                    retriever.embeddings = self.embeddings
                    retriever._cargado = True

                    def previous_search(text, predicate, k):
                        return self.store.similarity_search_with_score(
                            text, k=k, filter=predicate, fetch_k=self.store.index.ntotal
                        )

                    with patch.object(retriever, "_buscar_pre_filtrado", side_effect=previous_search):
                        expected = retriever.search_filtered(query, **filters)
                    actual = retriever.search_filtered(query, **filters)
                    self.assertNotIn("Error al realizar", expected)
                    self.assertNotIn("Error al realizar", actual)
                    self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
