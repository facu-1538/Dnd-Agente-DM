"""
retriever.py - Módulo de recuperación de información RAG para D&D 5e AI Dungeon Master.

Proporciona la clase RAGRetriever con patrón Singleton para cargar y consultar
el vectorstore FAISS construido por setup_rag.py.
"""

import os
import re
from pathlib import Path
from dotenv import load_dotenv

from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from rag.metadata import normalizar_escuela, normalizar_tipo

# Cargar variables de entorno
load_dotenv()

# Ruta al vectorstore guardado en disco
VECTOR_DB_DIR = Path("storage/vector_db")

# Variable global para el patrón Singleton
_instance = None


class RAGRetriever:
    """
    Clase que encapsula la lógica de recuperación de información del vectorstore FAISS.

    Implementa el patrón Singleton para evitar cargar el vectorstore múltiples
    veces en memoria durante la misma sesión de la aplicación.

    Uso:
        retriever = RAGRetriever.get_instance()
        resultado = retriever.search("¿Qué es un Dragón Rojo?")
    """

    @classmethod
    def get_instance(cls) -> "RAGRetriever":
        """
        Retorna la instancia única del RAGRetriever (patrón Singleton).

        Si la instancia no existe, la crea. Si ya existe, retorna la misma.

        Returns:
            Instancia única de RAGRetriever.
        """
        global _instance
        if _instance is None:
            _instance = cls()
        return _instance

    def __init__(self):
        """
        Inicializa el RAGRetriever con vectorstore y embeddings vacíos.

        No carga el vectorstore aquí para evitar errores si aún no fue creado.
        La carga se hace de forma lazy al primer uso.
        """
        # El vectorstore FAISS (se carga con load())
        self.vectorstore = None
        # El modelo de embeddings de Google Gemini
        self.embeddings = None
        # Bandera para saber si ya se intentó cargar
        self._cargado = False
        # (categoria, titulo en minúsculas) -> primer fragmento de esa entrada; se arma al primer uso
        self._titulos = None

    def load(self) -> bool:
        """
        Carga el vectorstore FAISS desde el disco.

        Lee el vectorstore guardado en storage/vector_db/ e inicializa
        el modelo de embeddings necesario para realizar búsquedas.

        Returns:
            True si se cargó exitosamente, False si el vectorstore no existe.

        Raises:
            ValueError: Si GEMINI_API_KEY no está configurada.
        """
        # Verificar que existe la API key
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY no encontrada. Verifica tu archivo .env"
            )

        # Verificar que existe el directorio del vectorstore
        if not VECTOR_DB_DIR.exists():
            print(
                f"Advertencia: El vectorstore no existe en {VECTOR_DB_DIR}. "
                "Ejecuta setup_rag.py para crearlo."
            )
            self._cargado = True  # Marcar como intentado para no reintentar
            return False

        try:
            # Determinar el modelo de embeddings usado al construir el vectorstore
            archivo_modelo = VECTOR_DB_DIR / "modelo.txt"
            modelo_embedding = "gemini-embedding-2"
            if archivo_modelo.exists():
                modelo_embedding = archivo_modelo.read_text().strip()

            # Inicializar el modelo de embeddings
            self.embeddings = GoogleGenerativeAIEmbeddings(
                model=modelo_embedding,
                google_api_key=api_key
            )

            # Cargar el vectorstore FAISS desde disco
            # allow_dangerous_deserialization=True es necesario para cargar desde pickle
            self.vectorstore = FAISS.load_local(
                str(VECTOR_DB_DIR),
                self.embeddings,
                allow_dangerous_deserialization=True
            )

            self._cargado = True
            print(f"✓ Vectorstore RAG cargado desde {VECTOR_DB_DIR}")
            return True

        except Exception as e:
            print(f"Error al cargar el vectorstore: {e}")
            self._cargado = True  # Evitar reintentos automáticos
            return False

    def _ensure_loaded(self) -> bool:
        """
        Asegura que el vectorstore esté cargado antes de realizar búsquedas.

        Si aún no se intentó cargar, llama a load(). Si ya se intentó y falló,
        no reintenta para evitar múltiples errores.

        Returns:
            True si el vectorstore está disponible, False en caso contrario.
        """
        if not self._cargado:
            return self.load()
        return self.vectorstore is not None

    @staticmethod
    def _fuente(doc) -> str:
        """
        Identifica de dónde viene un fragmento: "archivo.txt › Título".

        La metadata `categoria` es el nombre del documento del corpus (rag/data/<categoria>.txt)
        y `titulo` es la entrada (hechizo, monstruo, regla) dentro de ese documento.
        """
        archivo = f"{doc.metadata.get('categoria', 'desconocido')}.txt"
        titulo = doc.metadata.get("titulo", "")
        return f"{archivo} › {titulo}" if titulo else archivo

    def _formatear(self, documentos: list) -> str:
        """
        Arma el texto que recibe el agente: cada fragmento con un encabezado que nombra
        su documento de origen, y al final la lista de fuentes consultadas, para que el
        modelo pueda citarlas literalmente en su respuesta.
        """
        partes = []
        fuentes = []
        for i, doc in enumerate(documentos, 1):
            fuente = self._fuente(doc)
            if fuente not in fuentes:           # sin repetir, respetando el orden
                fuentes.append(fuente)
            partes.append(f"[Fuente {i}: {fuente}]\n{doc.page_content}")

        return (
            "\n\n---\n\n".join(partes)
            + "\n\nFUENTES CONSULTADAS: " + "; ".join(fuentes)
        )

    # ------------------------------------------------------------------
    # Búsqueda: similitud vectorial + filtros por metadatos + coincidencia de título
    # ------------------------------------------------------------------
    def _indice_titulos(self) -> dict:
        """Primer fragmento de cada entrada del corpus, indexado por (categoría, título en minúsculas)."""
        if self._titulos is None:
            self._titulos = {}
            for doc in self.vectorstore.docstore._dict.values():
                clave = (doc.metadata.get("categoria"), str(doc.metadata.get("titulo", "")).lower())
                self._titulos.setdefault(clave, doc)   # el docstore conserva el orden: primero va el 1.er fragmento
        return self._titulos

    @staticmethod
    def _crear_filtro(categoria, nivel, escuela, tipo, cr_max):
        """Función que decide, por los metadatos de un fragmento, si cumple todos los filtros pedidos."""
        def cumple(meta: dict) -> bool:
            if categoria and meta.get("categoria") != categoria:
                return False
            if nivel is not None and meta.get("nivel") != nivel:
                return False
            if escuela and meta.get("escuela") != escuela:
                return False
            if tipo and meta.get("tipo") != tipo:
                return False
            if cr_max is not None and (meta.get("cr") is None or meta["cr"] > cr_max):
                return False
            return True
        return cumple

    def _coincidencias_titulo(self, query: str, filtro, categoria, maximo: int = 2) -> list:
        """
        Entradas cuyo título aparece en la consulta ("Fireball", "wizard"): se priorizan sobre la
        similitud porque son lo que el jugador nombró. Evita falsos positivos con títulos cortos
        ("Light", "Aid"): un título de una sola palabra solo cuenta si tiene 5 letras o más y la
        consulta es corta, o si el agente ya acotó la búsqueda a esa categoría.
        """
        q = query.lower()
        palabras = len(q.split())
        candidatos = []
        for (cat, titulo), doc in self._indice_titulos().items():
            if not titulo or not filtro(doc.metadata):
                continue
            if not re.search(rf"(?<!\w){re.escape(titulo)}(?!\w)", q):
                continue
            if " " not in titulo and not categoria and (len(titulo) < 5 or palabras > 3):
                continue
            candidatos.append((len(titulo), doc))
        candidatos.sort(key=lambda x: -x[0])             # el título más largo es el más específico
        return [d for _, d in candidatos[:maximo]]

    def search_filtered(self, query: str, k: int = 4, category: str | None = None,
                        spell_level: int | None = None, school: str | None = None,
                        monster_type: str | None = None, max_cr: float | None = None) -> str:
        """
        Búsqueda híbrida (self-query): similitud vectorial restringida por metadatos.

        Los filtros se aplican sobre TODO el índice (fetch_k = tamaño del índice); FAISS filtra después
        de traer los más cercanos, así que con el valor por defecto (20) una categoría chica casi nunca
        devolvía nada. Los fragmentos cuyo título aparece en la consulta van primero. El total nunca
        supera k, para no saturar el contexto del agente.

        Args:
            query: Consulta en lenguaje natural.
            k: Cantidad máxima de fragmentos a devolver.
            category: spells, monsters, conditions, armor, weapons, rules o classes.
            spell_level: Nivel del hechizo (0 = truco).
            school: Escuela de magia (evocation, necromancy...; acepta español).
            monster_type: Tipo de criatura (dragon, undead...; acepta español).
            max_cr: Desafío máximo del monstruo (0.25 = CR 1/4).
        """
        if not self._ensure_loaded():
            return (
                "No hay base de conocimientos D&D disponible. "
                "Ejecuta setup_rag.py para crear el vectorstore."
            )
        try:
            escuela = normalizar_escuela(school) if school else None
            tipo = normalizar_tipo(monster_type) if monster_type else None
            filtros = {n: v for n, v in (("categoría", category), ("nivel", spell_level), ("escuela", escuela),
                                         ("tipo", tipo), ("CR máx", max_cr)) if v is not None and v != ""}
            if filtros:
                filtro = self._crear_filtro(category or None, spell_level, escuela, tipo, max_cr)
                similares = self.vectorstore.similarity_search(
                    query, k=k, filter=filtro, fetch_k=self.vectorstore.index.ntotal
                )
                por_titulo = self._coincidencias_titulo(query, filtro, category)
            else:
                similares = self.vectorstore.similarity_search(query, k=k)
                por_titulo = self._coincidencias_titulo(query, lambda m: True, None)

            documentos, vistos = [], set()
            for doc in por_titulo + similares:           # primero lo nombrado, luego lo parecido
                huella = doc.metadata.get("hash") or id(doc)
                if huella not in vistos:
                    vistos.add(huella)
                    documentos.append(doc)
            documentos = documentos[:k]

            if not documentos:
                if filtros:
                    aplicados = ", ".join(f"{n}={v}" for n, v in filtros.items())
                    return (f"Sin resultados con los filtros aplicados ({aplicados}). "
                            "Prueba quitando algún filtro o con otra categoría.")
                return "No se encontró información relevante sobre ese tema en la base de conocimientos."
            return self._formatear(documentos)

        except Exception as e:
            return f"Error al realizar la búsqueda: {e}"

    def search(self, query: str, k: int = 4) -> str:
        """Búsqueda general (sin filtros) en todo el corpus. Ver search_filtered()."""
        return self.search_filtered(query, k=k)

    def search_by_category(self, query: str, category: str, k: int = 4) -> str:
        """
        Búsqueda restringida a una categoría (spells, monsters, conditions, armor, weapons, rules, classes).
        Si la categoría no existe o no tiene resultados, hace una búsqueda general.
        """
        resultado = self.search_filtered(query, k=k, category=category)
        if resultado.startswith("Sin resultados con los filtros"):
            print(f"  No se encontraron resultados en categoría '{category}', buscando en general...")
            return self.search(query, k=k)
        return resultado


def get_retriever() -> RAGRetriever:
    """
    Función auxiliar que retorna la instancia Singleton del RAGRetriever.

    Esta función es el punto de entrada recomendado para obtener el retriever.
    Garantiza que siempre se use la misma instancia en toda la aplicación.

    Returns:
        Instancia única y compartida de RAGRetriever.

    Ejemplo:
        from rag.retriever import get_retriever

        retriever = get_retriever()
        info = retriever.search("¿Cuántos HP tiene un Dragón Rojo Anciano?")
    """
    return RAGRetriever.get_instance()
