"""
retriever.py - Módulo de recuperación de información RAG para D&D 5e AI Dungeon Master.

Proporciona la clase RAGRetriever con patrón Singleton para cargar y consultar
el vectorstore FAISS construido por setup_rag.py.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

from langchain_community.vectorstores import FAISS
from langchain_google_genai import GoogleGenerativeAIEmbeddings

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

    def search(self, query: str, k: int = 4) -> str:
        """
        Realiza una búsqueda por similitud semántica en el vectorstore.

        Busca los k documentos más relevantes para la consulta dada y retorna
        su contenido concatenado como texto.

        Args:
            query: La consulta o pregunta a buscar.
            k: Número de documentos más relevantes a retornar (por defecto 4).

        Returns:
            Texto concatenado de los documentos encontrados, con separadores.
            Si no hay vectorstore disponible o no se encuentran resultados,
            retorna un mensaje descriptivo.
        """
        # Verificar que el vectorstore esté disponible
        if not self._ensure_loaded():
            return (
                "No hay base de conocimientos D&D disponible. "
                "Ejecuta setup_rag.py para crear el vectorstore."
            )

        try:
            # Realizar búsqueda por similitud
            documentos = self.vectorstore.similarity_search(query, k=k)

            if not documentos:
                return "No se encontró información relevante sobre ese tema en la base de conocimientos."

            # Concatenar el contenido de los documentos con separadores
            partes = []
            for i, doc in enumerate(documentos, 1):
                # Incluir metadata si está disponible
                categoria = doc.metadata.get("categoria", "")
                titulo = doc.metadata.get("titulo", "")

                encabezado = f"[Fuente {i}"
                if categoria:
                    encabezado += f" - {categoria}"
                if titulo:
                    encabezado += f": {titulo}"
                encabezado += "]"

                partes.append(f"{encabezado}\n{doc.page_content}")

            return "\n\n---\n\n".join(partes)

        except Exception as e:
            return f"Error al realizar la búsqueda: {e}"

    def search_by_category(self, query: str, category: str, k: int = 4) -> str:
        """
        Realiza una búsqueda por similitud filtrada por categoría.

        Similar a search(), pero intenta filtrar los resultados por la categoría
        indicada (por ejemplo, "spells", "monsters", "conditions").

        Args:
            query: La consulta o pregunta a buscar.
            category: Categoría por la que filtrar (debe coincidir con la metadata).
            k: Número de documentos más relevantes a retornar (por defecto 4).

        Returns:
            Texto concatenado de los documentos encontrados, con separadores.
            Si la categoría no produce resultados, hace una búsqueda general.
        """
        # Verificar que el vectorstore esté disponible
        if not self._ensure_loaded():
            return (
                "No hay base de conocimientos D&D disponible. "
                "Ejecuta setup_rag.py para crear el vectorstore."
            )

        try:
            # Intentar búsqueda con filtro de metadata por categoría
            # FAISS soporta filtros de metadata con similarity_search_with_score
            # pero la implementación exacta depende de la versión de langchain
            try:
                documentos = self.vectorstore.similarity_search(
                    query,
                    k=k * 3,  # Buscar más resultados para filtrar después
                    filter={"categoria": category}
                )
            except (TypeError, NotImplementedError):
                # Si el filtro no está soportado, buscar sin filtro y filtrar manualmente
                todos = self.vectorstore.similarity_search(query, k=k * 3)
                documentos = [
                    doc for doc in todos
                    if doc.metadata.get("categoria", "").lower() == category.lower()
                ]

            # Si no hay resultados con el filtro, hacer búsqueda general
            if not documentos:
                print(f"  No se encontraron resultados en categoría '{category}', buscando en general...")
                return self.search(query, k=k)

            # Limitar a k resultados
            documentos = documentos[:k]

            # Concatenar el contenido de los documentos con separadores
            partes = []
            for i, doc in enumerate(documentos, 1):
                titulo = doc.metadata.get("titulo", "")
                encabezado = f"[{category.capitalize()} {i}"
                if titulo:
                    encabezado += f": {titulo}"
                encabezado += "]"

                partes.append(f"{encabezado}\n{doc.page_content}")

            return "\n\n---\n\n".join(partes)

        except Exception as e:
            return f"Error al realizar la búsqueda por categoría: {e}"


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
