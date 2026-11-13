# Dungeon Master AI: agente de D&D 5e con LangChain

Trabajo Integrador de **Modelos de Lenguaje** (UTN FRM, 2026).

Un Dungeon Master virtual construido con un **agente de LangChain** (`create_agent`). Narra la aventura, consulta el reglamento oficial (SRD 5.1) con **RAG**, resuelve el azar con tiradas de dados reales, mantiene la vida del personaje en un estado persistente y dibuja el **mapa del dungeon**, que se puede descargar como PNG. La interfaz web muestra **en vivo** qué hace el agente: su razonamiento, cada herramienta con sus argumentos y el resultado, y la respuesta final.

## Qué resuelve

**Dominio:** dirigir una partida de rol de Dungeons & Dragons 5ª edición, con reglas reales y estado de juego consistente.

**Usuario:** un jugador (o un grupo) que quiere jugar sin un DM humano y sin que el modelo "invente" las reglas, las tiradas o la vida del personaje.

**Tarea de ejemplo (varios pasos, no resoluble con una sola llamada ni una sola tool):**

> *"Entro a la armería."*
>
> 1. El agente **consulta el reglamento** (RAG) para obtener las estadísticas del enemigo que aparece.
> 2. **Tira los dados** del ataque contra la CA del jugador (y el daño si impacta).
> 3. **Aplica el daño** al estado real del personaje (la barra de vida de la UI baja).
> 4. **Revela la sala en el mapa** y lo vuelve a renderizar.
> 5. Narra el resultado y deja el mapa listo para **descargar** (producto final tangible).

## Arquitectura

```mermaid
flowchart LR
    U[Jugador] --> UI["Streamlit (app.py)<br/>chat + sidebar + mapa"]
    UI -->|"stream_agent()"| A

    subgraph A["Agente: create_agent (LangChain / LangGraph)"]
        M["Modelo<br/>Gemini 3.1 Flash-Lite"] <-->|"tool calls"| T["Herramientas"]
        CP[("InMemorySaver<br/>memoria por thread_id")] --- M
    end

    T --> R["query_rules_rag"] --> V[("FAISS<br/>SRD 5.1")]
    T --> D["execute_dice_roll"]
    T --> H["damage_player / heal_player"] --> S[("game_state.json")]
    T --> P["switch_and_render_map"] --> MAP[("mapa PNG")]

    A -.->|"eventos: razonamiento,<br/>tool_call, tool_result, texto"| UI
```

### Componentes y por qué están

| Componente | Qué es | Por qué |
|---|---|---|
| **Agente** | `create_agent` de LangChain (sin Deep Agents) | Resuelve el bucle modelo ↔ herramientas con tool calling. |
| **Modelo** | `gemini-3.1-flash-lite` (Google Gemini) | Soporta tool calling (verificado), tiene tier gratuito y devuelve resúmenes de razonamiento. |
| **Memoria de corto plazo** | `InMemorySaver` (checkpointer de LangGraph), por `thread_id` | Permite turnos que dependen de los anteriores ("¿cuánto salió en esa tirada?"). Se pierde al reiniciar el proceso. |
| **RAG** | FAISS + embeddings `gemini-embedding-2` sobre 6 documentos del SRD | El modelo no memoriza las reglas con fidelidad; el RAG aporta hechizos, monstruos y reglas exactas. |
| **UI** | Streamlit | Permite chat con markdown y mostrar el flujo del agente en streaming (ver §3.5 de la consigna). |
| **Middleware** | **Ninguno** | Los reintentos ante errores 429/5xx los resuelve el cliente de Gemini (`max_retries=6`, backoff exponencial de 1 s a 60 s) y el tope de pasos se fija con `recursion_limit`. No hacía falta código ni middleware adicional. |

### Herramientas

| Tool | Tipo | Qué hace | Entradas → Salida |
|---|---|---|---|
| `query_rules_rag` | **Externa** (base vectorial) | Busca en el SRD hechizos, monstruos, condiciones, armas, armaduras y reglas. | `query: str`, `category: str` (`spells`, `monsters`, `conditions`, `armor`, `weapons`, `rules` o `general`) → fragmentos, cada uno con su documento de origen (`archivo.txt › Título`) y la línea `FUENTES CONSULTADAS` |
| `execute_dice_roll` | **Propia** | Ejecuta tiradas reales (`1d20+5`, `2d6`, `4d6kh3`) y compara con CA/CD. | `dice_formula`, `reason`, `target_dc_or_ac` → JSON con dados, total y éxito |
| `damage_player` / `heal_player` | **Propias** | Modifican la vida del jugador en el estado real (`storage/game_state.json`). | `amount: int`, `source: str` → JSON con HP anterior y nuevo |
| `switch_and_render_map` | **Propia** + externa (archivo) | Revela salas (validadas contra el layout) y renderiza el mapa con niebla de guerra. | `revealed_nodes: list[str]` → texto con salas nuevas y PNG en `storage/maps/` |

Todas manejan sus errores sin cortar el flujo (devuelven un mensaje/JSON de error que el agente puede leer).

## Estructura del repositorio

```
app.py                  # UI Streamlit: chat, streaming, sidebar y mapa
agent/
  core.py               # create_agent, checkpointer, stream_agent() / run_agent()
  tools.py              # las 5 herramientas
  prompt.py             # system prompt del DM + reglas
game_engine/
  state_manager.py      # estado de la partida (vida, oro, salas, enemigos)
  map_renderer.py       # layout del dungeon y mapa con niebla (Pillow)
rag/
  data/*.txt            # corpus (6 documentos del SRD)
  retriever.py          # búsqueda en FAISS
setup_rag.py            # ingesta + chunking + embeddings + índice
storage/vector_db/      # índice FAISS ya construido (incluido en el repo)
.streamlit/config.toml  # tema de la UI
ui/style.css            # tipografía de lectura de la narración
```

## Instalación y ejecución

**Requisitos:** Python 3.11 o superior (es el mínimo que exigen las dependencias; el proyecto se probó con Python 3.14 en Windows 11) y una API key de Google Gemini.

### 1. Clonar y crear el entorno virtual

```bash
git clone https://github.com/facu-1538/Dnd-Agente-DM.git
cd Dnd-Agente-DM
python -m venv venv
```

Activarlo:

```bash
# Windows PowerShell
.\venv\Scripts\Activate.ps1
# Windows CMD
venv\Scripts\activate.bat
# Linux / macOS
source venv/bin/activate
```

### 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 3. Configurar la API key

Obtené una clave gratis en <https://aistudio.google.com/app/apikey>, copiá el archivo de ejemplo y editalo:

```bash
# Windows
copy .env.example .env
# Linux / macOS
cp .env.example .env
```

En `.env` dejá la clave **sin comillas**:

```
GEMINI_API_KEY=tu_clave_real
```

`.env` está en `.gitignore`: no lo subas nunca. Variables opcionales (se agregan al mismo `.env`):

| Variable | Por defecto | Para qué |
|---|---|---|
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | Cambiar el modelo del agente (debe soportar tool calling). |
| `DM_INCLUDE_THOUGHTS` | `1` | Con `0` no se pide el razonamiento al modelo. |

### 4. Ejecutar

```bash
streamlit run app.py
```

Se abre en <http://localhost:8501>. El índice vectorial ya viene en el repositorio, así que **no hace falta ejecutar `setup_rag.py`** para jugar.

> Los tiers gratuitos tienen límite de peticiones. El primer turno puede tardar (carga del índice). Si ves un error 429, esperá un minuto.

## Cómo demostrar cada requisito

| Requisito | Qué hacer en la UI |
|---|---|
| **Tarea de varios pasos** | Escribí `Un goblin me ataca en la armería.` y mirá la secuencia de herramientas. |
| **Visibilidad de tools (streaming)** | Cada herramienta aparece en la línea de tiempo apenas se invoca, con sus argumentos; al terminar muestra el resumen del resultado. Al abrirla se ve el resultado completo. |
| **Razonamiento** | Bloque plegable "Razonamiento", diferenciado de la respuesta. Se muestra en vivo mientras el modelo piensa. |
| **Memoria** | Después de una tirada preguntá `¿Cuánto salió en esa tirada?`: responde sin que se lo repitas. |
| **RAG y citas** | Preguntá `¿Qué hace el hechizo Bola de Fuego?`: aparece la consulta de reglas con sus **fuentes consultadas** (al abrir el paso), y la respuesta cita el documento, por ejemplo `(Fuente: spells.txt › Fireball)`. |
| **Producto tangible** | Tras explorar salas, el botón **Descargar mapa** (en el chat y en la sidebar) exporta el PNG. |

## RAG: corpus e ingesta (reproducible)

**Corpus:** 6 documentos de texto en `rag/data/`: `spells`, `monsters`, `conditions`, `weapons`, `armor` y `rules`. Provienen del SRD 5.1 de Wizards of the Coast a través de la API pública [Open5e](https://open5e.com/).

**Pipeline** (`setup_rag.py`):

1. **Ingesta:** descarga cada categoría de la API Open5e y la guarda como Markdown en `rag/data/*.txt`. Si esos archivos ya existen, se omite la descarga.
2. **Chunking:** primero por encabezados (`##`, una entrada por hechizo o monstruo) y luego `RecursiveCharacterTextSplitter` con `chunk_size=2000` y `chunk_overlap=200`.
3. **Embeddings:** `gemini-embedding-2` por lotes de 50, con pausas para respetar el límite por minuto.
4. **Índice:** FAISS local en `storage/vector_db/`. Cada fragmento guarda su `categoria` (= archivo de origen) y su `titulo`.

**Tamaño del contexto:** la tool devuelve como máximo 4 fragmentos de ≤2000 caracteres (~8000 caracteres), para no saturar el contexto del agente.

**Citas de las fuentes:** cada fragmento que recibe el agente lleva un encabezado con su documento de origen (`[Fuente 1: monsters.txt › Goblin]`) y la tool cierra con la línea `FUENTES CONSULTADAS`. El prompt del DM (`CITAS_RAG` en `agent/prompt.py`) le exige citar entre paréntesis, con ese mismo formato, todo dato de reglas que use, y no citar nada que no haya salido de la herramienta. La UI muestra la lista de fuentes al abrir el paso de la consulta. Como la búsqueda siempre devuelve 4 fragmentos, algunos pueden no ser relevantes: el modelo cita solo los que usa.

**Reconstruir desde cero:**

```bash
# Volver a descargar el corpus desde Open5e e indexar los fragmentos que falten
python setup_rag.py --force
```

Para regenerar el índice completo, borrá `storage/vector_db/` y ejecutá `python setup_rag.py`. El script indexa solo los fragmentos que todavía no están (los identifica por un hash de su contenido). El proceso tarda varios minutos y puede agotar la cuota diaria de embeddings: guarda el progreso y se reanuda al volver a ejecutarlo.

## Visibilidad del razonamiento

`gemini-3.1-flash-lite` solo devuelve razonamiento si se activa el pensamiento dinámico (`thinking_budget=-1`), que ya viene configurado en `agent/core.py`. Gemini entrega un **resumen** del razonamiento, **en inglés**, no la cadena de pensamiento completa. En turnos simples puede no emitir ningún bloque; en ese caso la UI no muestra nada.

## Limitaciones conocidas

- **Memoria en proceso:** el `InMemorySaver` se pierde al reiniciar Streamlit. Para persistirla bastaría cambiarlo por un checkpointer de SQLite.
- **Dungeon fijo:** el mapa tiene 8 salas definidas en `game_engine/map_renderer.py`; el agente solo decide cuáles revelar.
- **Ficha del personaje:** la creación de personaje se conversa con el DM, pero no se guarda en la ficha de la sidebar (nombre, clase y nivel son los del estado por defecto).
- **Cuota gratuita:** los límites de Gemini pueden producir errores 429 o latencias altas; los reintentos no ayudan si se agota la cuota diaria.
- **Idioma del razonamiento:** llega en inglés (resumen del proveedor).
- **Índice FAISS:** se carga con `allow_dangerous_deserialization=True` (formato pickle). Es seguro porque el índice lo generamos nosotros, pero no cargues índices de terceros.

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `GEMINI_API_KEY no configurada` | Falta `.env` o la variable. Copiá `.env.example` y completá la clave. |
| Error 429 / "quota" | Límite del tier gratuito. Esperá un minuto; si es la cuota diaria, probá más tarde. |
| El modelo no existe (404) | Cambiá `GEMINI_MODEL` por un modelo disponible para tu cuenta. |
| Falla al arrancar con el razonamiento | Poné `DM_INCLUDE_THOUGHTS=0` en `.env`. |
| `Activate.ps1` no se ejecuta | Usá CMD con `activate.bat`, o habilitá scripts en PowerShell. |
| El RAG no responde | Verificá que exista `storage/vector_db/` o regeneralo con `python setup_rag.py`. |

## Referencias

- [LangChain: agentes](https://docs.langchain.com/oss/python/langchain/agents)
- [LangGraph: persistencia (checkpointers)](https://docs.langchain.com/oss/python/langgraph/persistence)
- [Streamlit](https://docs.streamlit.io/)
- [Open5e API](https://open5e.com/) y System Reference Document 5.1 (Wizards of the Coast, licencia abierta OGL 1.0a / CC-BY-4.0)
- [Google AI Studio](https://aistudio.google.com/)

## Equipo

Integrantes: _(completar)_
