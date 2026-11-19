"""Reglas iniciales por necesidad/clase: selección exacta, sin embeddings ni top-k."""
import copy
import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

DATA_DIR = Path(__file__).resolve().parent / "data"
NEEDS = ("comparar_clases", "crear_personaje", "equipo_inicial", "magia_inicial",
         "tiradas_basicas", "primer_combate")


@lru_cache(maxsize=1)
def catalog() -> dict:
    return json.loads((DATA_DIR / "starting_rules.json").read_text(encoding="utf-8"))


def profiles() -> dict:
    return copy.deepcopy(catalog()["classes"])


def _normal(value: str) -> str:
    return unicodedata.normalize("NFD", value.strip().casefold()).encode("ascii", "ignore").decode()


def class_id(value: str) -> str:
    value = _normal(value)
    for key, profile in catalog()["classes"].items():
        if value in {key, _normal(profile["label"]), _normal(profile["title"])}:
            return key
    raise ValueError("Elegí guerrero, mago, pícaro o clérigo.")


@lru_cache(maxsize=8)
def corpus_documents(category: str) -> tuple:
    if category not in {"classes", "spells", "rules", "weapons", "armor", "conditions", "monsters"}:
        raise ValueError("Categoría desconocida")
    splitter = MarkdownHeaderTextSplitter(headers_to_split_on=[("##", "titulo")], strip_headers=False)
    docs = splitter.split_text((DATA_DIR / f"{category}.txt").read_text(encoding="utf-8"))
    for doc in docs:
        doc.metadata["categoria"] = category
    return tuple(RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=200).split_documents(docs))


def class_documents(class_names: list[str] | None = None) -> list:
    """Devuelve TODOS los chunks de las clases elegidas (14 para las cuatro iniciales)."""
    names = class_names or list(catalog()["classes"])
    titles = {catalog()["classes"][class_id(name)]["title"] for name in names}
    return [d for d in corpus_documents("classes") if d.metadata.get("titulo") in titles]


def classes_from_query(query: str) -> list:
    """Compatibilidad con category=classes: título/alias exacto, sin similitud vectorial."""
    q = _normal(query)
    titles = {d.metadata["titulo"] for d in corpus_documents("classes")}
    found = {title for title in titles if re.search(rf"(?<!\w){re.escape(_normal(title))}(?!\w)", q)}
    for profile in catalog()["classes"].values():
        if re.search(rf"(?<!\w){re.escape(_normal(profile['label']))}(?!\w)", q):
            found.add(profile["title"])
    return ([d for d in corpus_documents("classes") if d.metadata.get("titulo") in found]
            if found else class_documents())


def starting_documents(need: str, selected_class: str = "") -> list:
    if need not in NEEDS:
        raise ValueError("Necesidad desconocida: " + ", ".join(NEEDS))
    selected = class_id(selected_class) if selected_class else ""
    docs = [Document(page_content=f["text"], metadata={
        "categoria": "inicio", "titulo": f["id"], "reglamento": f["ruleset"],
        "fuente": f"SRD 5.1 / inicio › {f['id']}", "url": f["source"],
    }) for f in catalog()["fragments"]
        if f["need"] == need and (not f.get("class_id") or not selected or f["class_id"] == selected)]
    if need in {"comparar_clases", "crear_personaje"}:
        docs.extend(class_documents([selected] if selected else None))
    if need == "equipo_inicial":
        for key, profile in catalog()["classes"].items():
            if not selected or selected == key:
                docs.append(Document(page_content=json.dumps(profile["gear"], ensure_ascii=False),
                                     metadata={"categoria": "inicio", "titulo": f"{key}_equipo_2014"}))
    if need == "magia_inicial":
        names = set()
        for key, profile in catalog()["classes"].items():
            if not selected or selected == key:
                names.update(profile["cantrips"] + profile["spells"] + profile.get("domain_spells", []))
        docs.extend(d for d in corpus_documents("spells") if d.metadata.get("titulo") in names)
    return docs


def format_documents(docs: list) -> str:
    if not docs:
        return "Sin resultados para esos filtros de contenido inicial."
    parts, sources = [], []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("fuente") or f"{doc.metadata['categoria']}.txt › {doc.metadata['titulo']}"
        parts.append(f"[Fuente {i}: {source}]\n{doc.page_content}")
        if source not in sources:
            sources.append(source)
    return "\n\n---\n\n".join(parts) + "\n\nFUENTES CONSULTADAS: " + "; ".join(sources)
