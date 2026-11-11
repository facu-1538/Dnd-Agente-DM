# 🐉 D&D AI Dungeon Master

> **Trabajo Integrador Universitario**  
> Sistema de Dungeon Master automatizado con Inteligencia Artificial para Dungeons & Dragons 5ª Edición

---

## 📖 Descripción del Proyecto

Este proyecto implementa un **Dungeon Master virtual** basado en inteligencia artificial capaz de narrar aventuras de D&D 5e, aplicar reglas del juego, gestionar el estado de los personajes y los encuentros, y responder con coherencia narrativa a las acciones de los jugadores.

El sistema combina un **agente LangGraph** con un pipeline **RAG (Retrieval-Augmented Generation)** sobre el System Reference Document (SRD) de D&D 5e, utilizando el modelo **Gemini** de Google como motor de lenguaje. La interfaz es una aplicación web construida con **Streamlit**.

---

## 🏗️ Arquitectura del Sistema

```
┌─────────────────────────────────────────────────────────────────┐
│                        USUARIO / JUGADOR                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │  Entrada de texto (acción del jugador)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   INTERFAZ STREAMLIT (app.py)                   │
│   - Chat interactivo                                            │
│   - Panel lateral: ficha del personaje, estado del juego        │
│   - Historial de sesión                                         │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│               AGENTE LANGGRAPH (agent/dm_agent.py)              │
│                                                                 │
│   ┌─────────────┐    ┌──────────────┐    ┌──────────────────┐  │
│   │   Nodo DM   │───▶│ Nodo Routing │───▶│  Nodo de Acción  │  │
│   │  (Gemini)   │    │  (decisión)  │    │  (tool calling)  │  │
│   └─────────────┘    └──────────────┘    └────────┬─────────┘  │
│          ▲                                         │            │
│          └─────────────────────────────────────────┘            │
│                       Bucle ReAct                               │
└─────────────────────────────────────────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
┌─────────────────┐ ┌──────────────┐ ┌──────────────────────────┐
│   RAG TOOL      │ │  DICE TOOL   │ │    GAME ENGINE TOOL       │
│ (rag/retriever) │ │(game_engine/ │ │  (game_engine/combat,     │
│                 │ │  dice.py)    │ │   character, encounter)   │
│ - Busca reglas  │ │              │ │                           │
│   en el SRD     │ │ - Simula     │ │ - Gestiona HP, stats      │
│ - Hechizos      │ │   tiradas    │ │ - Combate por turnos      │
│ - Monstruos     │ │   de dados   │ │ - Chequeos de habilidad   │
│ - Clases/razas  │ │              │ │ - Estado del mundo        │
└────────┬────────┘ └──────────────┘ └──────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│              VECTOR STORE FAISS (rag/vector_store.py)           │
│   - Embeddings: sentence-transformers                           │
│   - Índice FAISS local                                          │
│   - Fuente: SRD 5e en texto plano                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ Stack Tecnológico

| Componente | Tecnología |
|---|---|
| **LLM** | Google Gemini (via `langchain-google-genai`) |
| **Orquestación del agente** | LangGraph |
| **Framework RAG** | LangChain + FAISS |
| **Embeddings** | `sentence-transformers` (local, sin costo) |
| **Interfaz de usuario** | Streamlit |
| **Almacenamiento vectorial** | FAISS (local) |
| **Gestión de entorno** | python-dotenv |

---

## ⚙️ Instalación Paso a Paso

### Prerrequisitos
- Python 3.10 o superior
- Una API key de Google Gemini (obtener en [Google AI Studio](https://aistudio.google.com/))

### 1. Clonar o descargar el proyecto

```bash
# Si usás git:
git clone <url-del-repositorio>
cd dnd-ai-dm

# O simplemente navegá a la carpeta del proyecto
cd C:\ruta\al\proyecto\dnd-ai-dm
```

### 2. Crear y activar el entorno virtual

```bash
# Crear el entorno virtual
python -m venv venv

# Activar en Windows (PowerShell)
.\venv\Scripts\Activate.ps1

# Activar en Windows (CMD)
venv\Scripts\activate.bat

# Activar en Linux/macOS
source venv/bin/activate
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

> ⚠️ La instalación de `faiss-cpu` y `sentence-transformers` puede tardar varios minutos ya que son bibliotecas pesadas.

### 4. Configurar la API key

Editá el archivo `.env` en la raíz del proyecto:

```
GEMINI_API_KEY=tu_clave_real_aqui
```

> **No uses comillas** alrededor de la clave. No subas este archivo a ningún repositorio público.

### 5. Construir el índice RAG

Este paso descarga los textos del SRD, genera los embeddings y construye el índice FAISS local. Solo necesitás hacerlo una vez:

```bash
python setup_rag.py
```

El script creará la carpeta `rag/vectorstore/` con el índice. Puede tardar entre 2 y 10 minutos según tu hardware.

### 6. Ejecutar la aplicación

```bash
streamlit run app.py
```

La aplicación se abrirá automáticamente en tu navegador en `http://localhost:8501`.

---

## 📁 Descripción de Módulos

### `app.py` — Interfaz principal
Punto de entrada de la aplicación. Define la UI de Streamlit: área de chat, panel lateral con la ficha del personaje, controles de sesión y visualización del historial de conversación.

### `agent/` — Agente LangGraph
- **`dm_agent.py`**: Define el grafo LangGraph del DM. Implementa el bucle ReAct (Razonar → Actuar → Observar) con nodos para la generación de respuesta, el enrutamiento a herramientas y la síntesis final.
- **`tools.py`**: Registra las herramientas disponibles para el agente: búsqueda RAG, tirada de dados, consulta del estado del juego, etc.
- **`prompts.py`**: Contiene los system prompts del DM que definen su personalidad, restricciones narrativas y formato de respuesta.

### `rag/` — Retrieval-Augmented Generation
- **`retriever.py`**: Implementa la herramienta de búsqueda semántica sobre el SRD. Dada una consulta (p. ej. "¿qué hace el hechizo Bola de Fuego?"), recupera los fragmentos más relevantes del índice FAISS.
- **`vector_store.py`**: Lógica para construir, guardar y cargar el índice vectorial FAISS.
- **`document_loader.py`**: Carga y divide los documentos del SRD en chunks apropiados para el indexado.

### `game_engine/` — Motor del Juego
- **`state.py`**: Define el estado global de la partida: personajes, HP, posición, inventario, encuentros activos.
- **`character.py`**: Gestión de personajes del jugador (stats, habilidades, equipo, niveles).
- **`dice.py`**: Simulador de dados (d4, d6, d8, d10, d12, d20, d100) con soporte para modificadores y ventaja/desventaja.
- **`combat.py`**: Motor de combate por turnos: iniciativa, ataques, daño, condiciones de estado.
- **`encounter.py`**: Generación y gestión de encuentros con monstruos del SRD.

### `setup_rag.py` — Script de configuración
Script de utilidad que descarga el texto del SRD, lo procesa, genera embeddings con `sentence-transformers` y construye el índice FAISS local. Debe ejecutarse una vez antes del primer uso.

---

## 🐛 Posibles Problemas y Soluciones

### Error: `GEMINI_API_KEY not found`
**Causa**: El archivo `.env` no existe, está vacío o la variable no está configurada.  
**Solución**: Verificá que el archivo `.env` está en la raíz del proyecto y contiene `GEMINI_API_KEY=tu_clave`.

### Error al instalar `faiss-cpu`
**Causa**: Incompatibilidad de versiones de Python o falta de dependencias de compilación en Windows.  
**Solución**: Intentá con `pip install faiss-cpu --only-binary :all:` o usá Python 3.10/3.11.

### Error: `No module named 'sentence_transformers'`
**Causa**: El entorno virtual no está activado o la instalación falló.  
**Solución**: Activá el venv y reinstalá: `pip install sentence-transformers`.

### El RAG no encuentra resultados relevantes
**Causa**: El índice FAISS no fue construido o está corrupto.  
**Solución**: Eliminá la carpeta `rag/vectorstore/` y volvé a ejecutar `python setup_rag.py`.

### Streamlit no abre el navegador automáticamente
**Causa**: Configuración del sistema operativo.  
**Solución**: Abrí manualmente `http://localhost:8501` en tu navegador.

### Respuestas lentas del agente
**Causa**: El modelo Gemini tiene latencia de red o se están recuperando muchos chunks del RAG.  
**Solución**: Reducí el parámetro `k` (número de chunks recuperados) en `rag/retriever.py`. Verificá tu conexión a internet.

---

## 📚 Referencias

- [Dungeons & Dragons 5e System Reference Document (SRD)](https://www.dndbeyond.com/srd)
- [LangChain Documentation](https://python.langchain.com/)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
- [Google Gemini API](https://ai.google.dev/)
- [Streamlit Documentation](https://docs.streamlit.io/)

---

## 👥 Autores

Trabajo Integrador Universitario — Sistemas de Inteligencia Artificial  
*Facultad de Ingeniería / Ciencias de la Computación*

---

## 📄 Licencia

Este proyecto es de uso académico. El contenido del SRD de D&D 5e está bajo la [Open Game License (OGL) v1.0a](https://www.dndbeyond.com/srd).
