"""
setup_rag.py - Script de ingesta de datos y construcción del vectorstore RAG
para el proyecto D&D 5e AI Dungeon Master.

Uso:
    python setup_rag.py

Requiere que GEMINI_API_KEY esté configurada en el archivo .env
"""

import requests
import json
import os
import sys
import time
import hashlib
from pathlib import Path
from dotenv import load_dotenv
from tqdm import tqdm

from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

# Cargar variables de entorno desde .env
load_dotenv()

from rag.metadata import completar_metadatos  # metadatos estructurados (nivel, escuela, tipo, CR...)
from rag.formatters import format_spell, format_monster, format_simple, format_condition, format_class

# URL base de la API Open5e (SRD D&D 5e)
BASE_URL = "https://api.open5e.com"

# Rutas de los directorios de datos y vectorstore
RAG_DATA_DIR = Path("rag/data")
VECTOR_DB_DIR = Path("storage/vector_db")


def fetch_all_pages(endpoint: str) -> list:
    """
    Obtiene todos los ítems de un endpoint de la API, manejando paginación.

    La API Open5e usa paginación con un campo 'next' que contiene la URL
    de la siguiente página. Se aplica retry con backoff exponencial en caso
    de errores de red.

    Args:
        endpoint: Ruta del endpoint, e.g. "/spells/" o "/monsters/"

    Returns:
        Lista con todos los ítems obtenidos de todas las páginas.
    """
    items = []
    separador = "&" if "?" in endpoint else "?"
    url = f"{BASE_URL}{endpoint}{separador}limit=100"
    intentos_max = 5

    while url:
        # Reintentos con backoff exponencial
        for intento in range(intentos_max):
            try:
                respuesta = requests.get(url, timeout=30)
                respuesta.raise_for_status()
                datos = respuesta.json()
                break
            except requests.exceptions.RequestException as e:
                if intento < intentos_max - 1:
                    espera = 2 ** intento  # 1, 2, 4, 8, 16 segundos
                    print(f"  Error al obtener {url}: {e}. Reintentando en {espera}s...")
                    time.sleep(espera)
                else:
                    print(f"  Falló después de {intentos_max} intentos: {url}")
                    return items

        # Agregar los resultados de esta página
        resultados = datos.get("results", [])
        items.extend(resultados)

        # Pasar a la siguiente página si existe
        url = datos.get("next")

    return items


def save_category(category: str, items: list, formatter) -> str:
    """
    Guarda una lista de ítems formateados en un archivo de texto.

    Args:
        category: Nombre de la categoría (usado como nombre de archivo).
        items: Lista de ítems a formatear y guardar.
        formatter: Función que convierte un ítem dict a string Markdown.

    Returns:
        Ruta del archivo guardado.
    """
    # Asegurarse de que el directorio existe
    RAG_DATA_DIR.mkdir(parents=True, exist_ok=True)

    ruta_archivo = RAG_DATA_DIR / f"{category}.txt"

    with open(ruta_archivo, "w", encoding="utf-8") as f:
        f.write(f"# {category.upper()} - D&D 5e SRD\n\n")
        for item in tqdm(items, desc=f"  Formateando {category}", leave=False):
            try:
                texto = formatter(item)
                f.write(texto)
                f.write("\n---\n\n")
            except Exception as e:
                nombre = item.get("name", "desconocido")
                print(f"  Advertencia: Error al formatear {nombre}: {e}")

    return str(ruta_archivo)


CATEGORIAS = ["spells", "monsters", "conditions", "weapons", "armor", "rules", "classes"]


def _falta(categoria: str, force: bool) -> bool:
    """True si hay que descargar esa categoría (no existe su .txt, está vacío o se pidió --force)."""
    ruta = RAG_DATA_DIR / f"{categoria}.txt"
    return force or not ruta.exists() or ruta.stat().st_size == 0


def ingest_data(force: bool = False):
    """
    Obtiene datos de la API Open5e y los guarda como archivos de texto Markdown.

    Es incremental: solo descarga las categorías cuyo .txt todavía no existe (o todas con
    force=True), para no repetir peticiones a la red.

    Categorías: hechizos (spells), monstruos (monsters), condiciones (conditions), armas (weapons),
    armaduras (armor), reglas (rules) y clases/subclases (classes).
    """
    print("=" * 60)
    print("INGESTA DE DATOS - D&D 5e AI Dungeon Master")
    print("=" * 60)
    print(f"Directorio de datos: {RAG_DATA_DIR.absolute()}")

    faltan = [c for c in CATEGORIAS if _falta(c, force)]
    if not faltan:
        print(f"\n✓ Omitiendo la descarga desde la API: ya existen los {len(CATEGORIAS)} documentos.")
        print("  (Para forzar una nueva descarga, ejecuta: python setup_rag.py --force)")
        return
    print(f"\nCategorías a descargar: {', '.join(faltan)}")

    # Cada categoría: (endpoint, formateador). weapons/armor/rules usan el formato genérico.
    fuentes = {
        "spells": ("/spells/?document__slug=wotc-srd", format_spell),
        "monsters": ("/monsters/?document__slug=wotc-srd", format_monster),
        "conditions": ("/v2/conditions/", format_condition),
        "weapons": ("/v2/weapons/?document__key=srd-2014",
                    lambda item: format_simple(item, ["damage_dice", "damage_type", "range", "long_range", "properties"])),
        "armor": ("/v2/items/?is_armor=true&document__key=srd-2014",
                  lambda item: format_simple(item, ["desc", "armor", "cost", "weight"])),
        "rules": ("/v2/rules/?document__key=srd-2014", lambda item: format_simple(item, ["desc"])),
        "classes": ("/v2/classes/?document__key=srd-2014", format_class),
    }
    for n, categoria in enumerate(faltan, 1):
        endpoint, formateador = fuentes[categoria]
        print(f"\n[{n}/{len(faltan)}] Obteniendo {categoria}...")
        datos = fetch_all_pages(endpoint)
        if datos:
            ruta = save_category(categoria, datos, formateador)
            print(f"  ✓ {len(datos)} entradas guardadas en {ruta}")
        else:
            print(f"  ✗ No se obtuvo {categoria}")


def es_cuota_diaria(error: Exception) -> bool:
    """Detecta si el error 429 es por el límite diario (no el del minuto)."""
    msg = str(error)
    return "PerDay" in msg or "per day" in msg


def build_vectorstore():
    """
    Construye (o continúa) el vectorstore FAISS con cache en disco.

    Guarda el índice después de cada lote y salta los fragmentos que ya
    están indexados, así se puede repartir el trabajo en varios días.
    Devuelve True si quedó completo, False si se cortó por la cuota diaria.
    """
    print("\n" + "=" * 60)
    print("CONSTRUCCIÓN DEL VECTORSTORE")
    print("=" * 60)

    MODELO = "gemini-embedding-2"

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY no encontrada. Verifica tu archivo .env")

    VECTOR_DB_DIR.mkdir(parents=True, exist_ok=True)

    # No mezclar embeddings de modelos distintos en el mismo índice
    archivo_modelo = VECTOR_DB_DIR / "modelo.txt"
    if archivo_modelo.exists() and archivo_modelo.read_text().strip() != MODELO:
        raise RuntimeError(
            "El índice existente se creó con otro modelo de embeddings. "
            "Borrá la carpeta storage/vector_db y volvé a correr."
        )
    archivo_modelo.write_text(MODELO)

    archivos_txt = list(RAG_DATA_DIR.glob("*.txt"))
    if not archivos_txt:
        raise FileNotFoundError(f"No hay archivos .txt en {RAG_DATA_DIR}.")

    # Dividir por encabezados y luego recortar los fragmentos muy largos
    splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("##", "titulo")],
        strip_headers=False,
    )
    docs = []
    for archivo in archivos_txt:
        categoria = archivo.stem
        contenido = archivo.read_text(encoding="utf-8")
        for doc in splitter.split_text(contenido):
            doc.metadata["categoria"] = categoria
            docs.append(doc)

    recortador = RecursiveCharacterTextSplitter(chunk_size=2000, chunk_overlap=200)
    docs = recortador.split_documents(docs)
    completar_metadatos(docs)   # nivel/escuela, tipo/CR, clase... desde la entrada completa (no cambia el hash)

    # Huella de cada fragmento (sin duplicados)
    unicos = {}
    for doc in docs:
        base = f"{doc.metadata.get('categoria')}|{doc.page_content}"
        huella = hashlib.sha1(base.encode("utf-8")).hexdigest()
        doc.metadata["hash"] = huella
        unicos[huella] = doc
    print(f"\nTotal de fragmentos únicos: {len(unicos)}")

    embeddings = GoogleGenerativeAIEmbeddings(model=MODELO, google_api_key=api_key)

    # Cargar lo que ya está indexado (si existe)
    vectorstore = None
    hechos = set()
    if (VECTOR_DB_DIR / "index.faiss").exists():
        vectorstore = FAISS.load_local(
            str(VECTOR_DB_DIR), embeddings, allow_dangerous_deserialization=True
        )
        hechos = {d.metadata.get("hash") for d in vectorstore.docstore._dict.values()}
        # Metadatos de lo ya indexado: se completan sin volver a calcular embeddings
        rellenados = completar_metadatos(list(vectorstore.docstore._dict.values()))
        if rellenados:
            vectorstore.save_local(str(VECTOR_DB_DIR))
            print(f"Metadatos completados en {rellenados} fragmentos ya indexados (sin gastar embeddings).")
    print(f"Ya indexados: {len(hechos)}")

    pendientes = [d for h, d in unicos.items() if h not in hechos]
    print(f"Pendientes:   {len(pendientes)}")

    if not pendientes:
        print("\n✓ No falta nada por indexar.")
        return True

    lote = 50
    for i in tqdm(range(0, len(pendientes), lote), desc="Embeddings"):
        grupo = pendientes[i:i + lote]
        for intento in range(5):
            try:
                if vectorstore is None:
                    vectorstore = FAISS.from_documents(grupo, embeddings)
                else:
                    vectorstore.add_documents(grupo)
                vectorstore.save_local(str(VECTOR_DB_DIR))  # guarda el progreso
                break
            except Exception as e:
                if es_cuota_diaria(e):
                    print("\n⚠ Se agotó la cuota DIARIA de embeddings.")
                    print("  El progreso quedó guardado. Volvé a correr mañana.")
                    return False
                espera = 30 * (intento + 1)
                print(f"  Error en el lote {i}: {str(e)[:120]}... Reintentando en {espera}s")
                time.sleep(espera)
        else:
            raise RuntimeError(f"Falló el lote que empieza en {i}")
        time.sleep(35)  # respetar el límite por minuto

    print(f"\n✓ Vectorstore completo en {VECTOR_DB_DIR.absolute()}")
    return True


if __name__ == "__main__":
    print("\n🐉 D&D 5e AI Dungeon Master - Setup RAG\n")

    # Verificar API key antes de comenzar
    load_dotenv()
    if not os.getenv("GEMINI_API_KEY"):
        print("ERROR: GEMINI_API_KEY no configurada.")
        print("Edita el archivo .env y agrega: GEMINI_API_KEY=tu_clave_aqui")
        exit(1)

    # Verificar si se solicitó forzar la descarga con --force
    force_download = "--force" in sys.argv

    # Paso 1: Ingestar datos desde la API (omite si ya existen archivos .txt)
    ingest_data(force=force_download)

    # Paso 2: Construir el vectorstore
    completo = build_vectorstore()

    if completo:
        print("\n🎉 Setup completado exitosamente.")
        print("Ahora puedes ejecutar: streamlit run app.py")
    else:
        print("\n⏳ Proceso pausado por cuota. Puedes continuar mañana ejecutando de nuevo este script.")
