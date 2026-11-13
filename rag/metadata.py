"""
metadata.py - Metadatos estructurados de cada entrada del corpus (hechizos, monstruos, clases).

Por qué existe: la búsqueda por similitud vectorial sola falla con preguntas que son, en el fondo, un
filtro ("hechizos de nivel 3 de evocación", "monstruos de CR menor a 2"). Si cada fragmento lleva esos
datos como metadatos, el agente los puede pedir como filtros (self-query) y el retriever los aplica.

Decisión importante: los metadatos se extraen del TEXTO COMPLETO DE LA ENTRADA (rag/data/*.txt) y se
copian a todos los fragmentos de esa entrada. No se leen del fragmento: el chunking de 2000 caracteres
parte las entradas largas y los fragmentos de continuación no traen la línea "**Nivel:**" ni "**Tipo:**".
Así, además, no cambia el contenido de ningún fragmento ni su hash, y no hay que volver a calcular
embeddings para tenerlos.
"""
import re
from pathlib import Path

RAG_DATA_DIR = Path("rag/data")

# Español -> inglés (el corpus está en inglés): el agente suele pedir "evocación" o "dragón".
ESCUELAS_ES = {
    "abjuracion": "abjuration", "conjuracion": "conjuration", "adivinacion": "divination",
    "encantamiento": "enchantment", "evocacion": "evocation", "ilusion": "illusion",
    "nigromancia": "necromancy", "transmutacion": "transmutation",
}
TIPOS_ES = {
    "aberracion": "aberration", "bestia": "beast", "celestial": "celestial", "constructo": "construct",
    "dragon": "dragon", "elemental": "elemental", "hada": "fey", "feerico": "fey", "demonio": "fiend",
    "infernal": "fiend", "gigante": "giant", "humanoide": "humanoid", "monstruosidad": "monstrosity",
    "cieno": "ooze", "limo": "ooze", "planta": "plant", "no muerto": "undead", "muerto viviente": "undead",
}


def _sin_tildes(texto: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFD", texto).encode("ascii", "ignore").decode()


def normalizar_escuela(valor: str) -> str:
    v = _sin_tildes(str(valor)).strip().lower()
    return ESCUELAS_ES.get(v, v)


def normalizar_tipo(valor: str) -> str:
    v = _sin_tildes(str(valor)).strip().lower()
    return TIPOS_ES.get(v, v)


def _campo(texto: str, etiqueta: str) -> str | None:
    m = re.search(rf"^\*\*{re.escape(etiqueta)}:\*\*\s*(.+)$", texto, flags=re.M)
    return m.group(1).strip() if m else None


def _nivel_hechizo(valor: str | None) -> int | None:
    if not valor:
        return None
    if valor.lower().startswith("cantrip"):
        return 0
    m = re.match(r"(\d+)", valor)
    return int(m.group(1)) if m else None


def _cr(valor: str | None) -> float | None:
    if not valor:
        return None
    try:
        if "/" in valor:
            a, b = valor.split("/")
            return float(a) / float(b)
        return float(valor)
    except ValueError:
        return None


def extraer_metadatos(categoria: str, texto_entrada: str) -> dict:
    """Metadatos estructurados de UNA entrada completa del corpus. Vacío si la categoría no los tiene."""
    meta: dict = {}
    if categoria == "spells":
        nivel = _nivel_hechizo(_campo(texto_entrada, "Nivel"))
        escuela = _campo(texto_entrada, "Escuela")
        if nivel is not None:
            meta["nivel"] = nivel
        if escuela:
            meta["escuela"] = escuela.lower()
    elif categoria == "monsters":
        tipo, cr = _campo(texto_entrada, "Tipo"), _cr(_campo(texto_entrada, "Desafío (CR)"))
        if tipo:
            meta["tipo"] = tipo.lower()
        if cr is not None:
            meta["cr"] = cr
    elif categoria == "classes":
        meta["subclase"] = _campo(texto_entrada, "Subclase de") is not None
        dado = _campo(texto_entrada, "Dado de golpe")
        if dado:
            meta["dado_golpe"] = dado.upper()
    return meta


def mapa_metadatos(directorio: Path = RAG_DATA_DIR) -> dict:
    """(categoria, titulo) -> metadatos, leyendo todos los .txt del corpus."""
    mapa: dict = {}
    for archivo in sorted(Path(directorio).glob("*.txt")):
        categoria = archivo.stem
        cuerpo = archivo.read_text(encoding="utf-8")
        for entrada in re.split(r"^## ", cuerpo, flags=re.M)[1:]:
            titulo, _, resto = entrada.partition("\n")
            meta = extraer_metadatos(categoria, resto)
            if meta:
                mapa[(categoria, titulo.strip())] = meta
    return mapa


def completar_metadatos(documentos: list, mapa: dict | None = None) -> int:
    """
    Copia a cada Document los metadatos de su entrada (según categoria y titulo). Idempotente.
    Devuelve cuántos documentos se modificaron.
    """
    mapa = mapa if mapa is not None else mapa_metadatos()
    cambiados = 0
    for doc in documentos:
        extra = mapa.get((doc.metadata.get("categoria"), doc.metadata.get("titulo")))
        if extra and any(doc.metadata.get(k) != v for k, v in extra.items()):
            doc.metadata.update(extra)
            cambiados += 1
    return cambiados
