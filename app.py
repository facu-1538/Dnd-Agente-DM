"""
app.py - Interfaz de Usuario del Dungeon Master AI
Aplicación Streamlit para el sistema D&D 5e AI Dungeon Master

Este módulo implementa la interfaz gráfica completa usando Streamlit,
conectando al agente LangGraph y mostrando el estado del juego en tiempo real.
"""

import streamlit as st
import os
import sys
import uuid
from pathlib import Path
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# ---------------------------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA (debe ser lo primero que se llama en Streamlit)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="⚔️ D&D AI Dungeon Master",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ---------------------------------------------------------------------------
# CSS PERSONALIZADO — Estética de mazmorra oscura con toques dorados
# ---------------------------------------------------------------------------
st.markdown("""
<style>

    @import url('https://fonts.googleapis.com/css2?family=Pixelify+Sans:wght@400;600&display=swap');

    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"],
    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] li,
    div[data-testid="stChatInput"] textarea {
        font-family: 'Pixelify Sans', 'Courier New', monospace !important;
        font-size: 1.2rem;
        letter-spacing: 0.3px;
        line-height: 1.5;
        color: #ffe066 !important;
    }
    /* ---- Fondo general ---- */
    .stApp {
        background-color: #1a1a2e;
        color: #e0d7c6;
    }

    /* ---- Sidebar ---- */
    [data-testid="stSidebar"] {
        background-color: #16213e;
        border-right: 2px solid #8b0000;
    }
    [data-testid="stSidebar"] * {
        color: #e0d7c6 !important;
    }

    /* ---- Título principal ---- */
    h1 {
        color: #ffd700 !important;
        font-family: 'Georgia', 'Times New Roman', serif;
        text-shadow: 2px 2px 8px rgba(139, 0, 0, 0.8);
        letter-spacing: 2px;
    }

    /* ---- Subtítulos ---- */
    h2, h3, h4 {
        color: #c9a84c !important;
        font-family: 'Georgia', serif;
    }

    /* ---- Mensajes de chat — asistente (DM) ---- */
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatar-assistant"]) {
        background-color: #0f3460 !important;
        border-left: 4px solid #8b0000;
        border-radius: 8px;
        padding: 12px;
        margin: 8px 0;
    }

    /* ---- Mensajes de chat — usuario ---- */
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatar-user"]) {
        background-color: #1f4068 !important;
        border-left: 4px solid #ffd700;
        border-radius: 8px;
        padding: 12px;
        margin: 8px 0;
    }

    /* ---- Input de chat ---- */
    [data-testid="stChatInput"] {
        background-color: #16213e;
        border: 2px solid #8b0000;
        border-radius: 8px;
        color: #e0d7c6;
    }
    [data-testid="stChatInput"] textarea {
        color: #e0d7c6 !important;
    }

    /* ---- Botones ---- */
    .stButton > button {
        background-color: #8b0000;
        color: #ffd700;
        border: 1px solid #ffd700;
        border-radius: 6px;
        font-family: 'Georgia', serif;
        font-weight: bold;
        transition: all 0.3s;
    }
    .stButton > button:hover {
        background-color: #ffd700;
        color: #1a1a2e;
        border-color: #8b0000;
    }

    /* ---- Métricas (HP) ---- */
    [data-testid="stMetric"] {
        background-color: #0f3460;
        border: 1px solid #8b0000;
        border-radius: 8px;
        padding: 8px;
    }
    [data-testid="stMetricValue"] {
        color: #ff4444 !important;
        font-size: 2rem !important;
        font-weight: bold !important;
    }
    [data-testid="stMetricLabel"] {
        color: #e0d7c6 !important;
    }

    /* ---- Barra de progreso (vida) ---- */
    .stProgress > div > div {
        background-color: #8b0000 !important;
    }

    /* ---- Expanders (tool calls) ---- */
    [data-testid="stExpander"] {
        background-color: #0f3460;
        border: 1px solid #c9a84c;
        border-radius: 6px;
    }

    /* ---- Caption / subtexto ---- */
    .stCaption {
        color: #c9a84c !important;
    }

    /* ---- Alertas ---- */
    .stAlert {
        border-radius: 8px;
    }

    /* ---- Separadores ---- */
    hr {
        border-color: #8b0000;
    }

    /* ---- Scroll del chat ---- */
    .main .block-container {
        padding-bottom: 100px;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# VERIFICACIÓN DE API KEY — Detenemos la app si no está configurada
# ---------------------------------------------------------------------------
if not os.getenv("GEMINI_API_KEY"):
    st.error("⚠️ **GEMINI_API_KEY** no configurada. Edita el archivo `.env` y agrega tu clave de API.")
    st.info("💡 Obtén tu clave en: https://aistudio.google.com/app/apikey")
    st.stop()

# ---------------------------------------------------------------------------
# IMPORTACIONES DE MÓDULOS PROPIOS (después de verificar la API key)
# ---------------------------------------------------------------------------

# Importar el motor de juego con manejo de errores
try:
    from game_engine.state_manager import get_state_manager
    _state_manager_available = True
except ImportError as e:
    _state_manager_available = False
    _state_manager_error = str(e)

# Importar el agente principal con manejo de errores
try:
    from agent.core import run_agent
    _agent_available = True
except ImportError as e:
    _agent_available = False
    _agent_error = str(e)

# Verificar si el vectorstore existe (RAG)
_vector_db_path = Path("storage/vector_db")
_vectorstore_available = _vector_db_path.exists() and any(_vector_db_path.iterdir())

# ---------------------------------------------------------------------------
# INICIALIZACIÓN DEL SESSION STATE
# ---------------------------------------------------------------------------

# Lista de mensajes del historial de la partida
if "messages" not in st.session_state:
    st.session_state.messages = []

# ID de hilo único por sesión (para la memoria del agente LangGraph)
if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())

# Flag para saber si se mostró el mensaje de bienvenida
if "welcomed" not in st.session_state:
    st.session_state.welcomed = False

# ---------------------------------------------------------------------------
# FUNCIONES AUXILIARES
# ---------------------------------------------------------------------------

def texto_de(contenido) -> str:
    """Extrae solo el texto de la respuesta, sea string o lista de bloques."""
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        partes = []
        for bloque in contenido:
            if isinstance(bloque, str):
                partes.append(bloque)
            elif isinstance(bloque, dict) and bloque.get("type") == "text":
                partes.append(bloque.get("text", ""))
        return "".join(partes)
    return str(contenido)


def renderizar_tool_calls(tool_calls: list):
    """
    Muestra las llamadas a herramientas en expanders con íconos temáticos.
    Se llama dentro del contexto de un st.chat_message.
    """
    for tc in tool_calls:
        nombre = tc.get("name", "herramienta")
        entrada = tc.get("input", {})
        salida = tc.get("output", "")

        # Elegir ícono y título según la herramienta
        if "rag" in nombre or "rules" in nombre or "query" in nombre:
            icono = "📚"
            titulo = "Consultando base de conocimientos..."
        elif "dice" in nombre or "roll" in nombre:
            icono = "🎲"
            titulo = f"Tirando dados..."
        elif "map" in nombre or "render" in nombre:
            icono = "🗺️"
            titulo = "Actualizando mapa del dungeon..."
        else:
            icono = "⚙️"
            titulo = f"Ejecutando: {nombre}"

        with st.expander(f"{icono} {titulo}", expanded=False):
            if entrada:
                st.markdown("**Parámetros:**")
                # Mostrar parámetros de forma legible
                for clave, valor in entrada.items():
                    st.markdown(f"- `{clave}`: `{valor}`")

            if salida:
                st.markdown("**Resultado:**")
                # Si es una tirada de dados, formatear bonito
                if "dice" in nombre or "roll" in nombre:
                    try:
                        import json
                        resultado = json.loads(salida) if isinstance(salida, str) else salida
                        if isinstance(resultado, dict):
                            total = resultado.get("total", resultado.get("result", "?"))
                            rolls = resultado.get("rolls", [])
                            formula = resultado.get("formula", entrada.get("formula", ""))
                            st.markdown(f"🎲 **Fórmula:** `{formula}`")
                            if rolls:
                                st.markdown(f"🎯 **Dados:** {rolls}")
                            st.markdown(f"✨ **Total:** `{total}`")
                        else:
                            st.code(str(salida))
                    except Exception:
                        st.code(str(salida))
                else:
                    # Truncar respuestas largas del RAG
                    texto = str(salida)
                    if len(texto) > 500:
                        st.markdown(texto[:500] + "...")
                    else:
                        st.markdown(texto)


def nueva_partida():
    """
    Reinicia el estado del juego y limpia el historial de mensajes.
    """
    # Limpiar el historial de chat
    st.session_state.messages = []
    st.session_state.welcomed = False
    st.session_state.thread_id = str(uuid.uuid4())

    # Reiniciar el estado del motor de juego
    if _state_manager_available:
        try:
            sm = get_state_manager()
            sm.reset_state()
        except Exception as e:
            st.sidebar.warning(f"No se pudo reiniciar el estado: {e}")

    st.rerun()


# ---------------------------------------------------------------------------
# SIDEBAR — Estado del Personaje
# ---------------------------------------------------------------------------

with st.sidebar:
    st.title("⚔️ Estado del Personaje")
    st.markdown("---")

    if _state_manager_available:
        state_manager = get_state_manager()
        try:
            hp = state_manager.get_player_hp()
            max_hp = state_manager.get_player_max_hp()
            info = state_manager.get_player_info()
        except Exception as e:
            st.warning(f"Error al cargar el estado: {e}")
            hp, max_hp, info = None, None, {}

        if hp is not None and max_hp:
            st.metric("❤️ Puntos de Vida", f"{hp} / {max_hp}")
            st.progress(max(0.0, min(1.0, hp / max_hp)))

            st.caption(
                f"{info.get('nombre', 'Aventurero')} · "
                f"{info.get('clase', '?')} · Nivel {info.get('nivel', '?')}"
            )
            st.write(f"💰 Oro: {info.get('oro', 0)}")
            st.write(f"⭐ Experiencia: {info.get('experiencia', 0)}")

        # --- Enemigos activos ---
        st.markdown("---")
        st.subheader("👹 Enemigos Activos")
        try:
            npcs = state_manager.get_active_npcs()
        except Exception:
            npcs = {}

        if npcs:
            for nombre_npc, datos_npc in npcs.items():
                hp_npc = datos_npc.get("hp", "?")
                max_hp_npc = datos_npc.get("max_hp", hp_npc)
                st.markdown(f"**{nombre_npc}** — ❤️ {hp_npc}/{max_hp_npc}")
                if isinstance(hp_npc, (int, float)) and isinstance(max_hp_npc, (int, float)) and max_hp_npc > 0:
                    st.progress(max(0.0, min(1.0, hp_npc / max_hp_npc)))
        else:
            st.markdown("*No hay enemigos en combate*")
    else:
        # Estado por defecto si el módulo no está disponible
        st.warning(f"⚠️ Motor de juego no disponible:\n`{_state_manager_error}`")
        st.metric(label="❤️ Puntos de Vida", value="N/A")

    st.markdown("---")

    # --- Mapa del Dungeon ---
    st.subheader("🗺️ Mapa del Dungeon")
    mapa_path = Path("storage/maps/current_view.png")
    if mapa_path.exists():
        st.image(str(mapa_path), width="stretch", caption="Vista actual del dungeon")
    else:
        st.markdown("""
<div style="
    background: #0f3460;
    border: 2px dashed #8b0000;
    border-radius: 8px;
    padding: 20px;
    text-align: center;
    color: #c9a84c;
    font-style: italic;
">
    🗺️ El mapa se revelará<br>al explorar el dungeon...
</div>
""", unsafe_allow_html=True)

    st.markdown("---")

    # --- Botón Nueva Partida ---
    if st.button("🔄 Nueva Partida", use_container_width=True):
        nueva_partida()

    # --- Información de la sesión ---
    with st.expander("ℹ️ Info de Sesión", expanded=False):
        st.markdown(f"**Thread ID:** `{st.session_state.thread_id[:8]}...`")
        st.markdown(f"**Mensajes:** {len(st.session_state.messages)}")
        st.markdown(f"**RAG:** {'✅ Disponible' if _vectorstore_available else '⚠️ No inicializado'}")
        st.markdown(f"**Agente:** {'✅ OK' if _agent_available else '❌ Error'}")

# ---------------------------------------------------------------------------
# PANEL PRINCIPAL
# ---------------------------------------------------------------------------

st.title("⚔️ D&D AI Dungeon Master")
st.caption("Powered by Google Gemini + LangChain + LangGraph")

# Aviso si el vectorstore no existe (permitir continuar de todas formas)
if not _vectorstore_available:
    st.warning(
        "⚠️ **Base de conocimientos RAG no inicializada.** "
        "El DM funcionará sin consultar el reglamento de D&D 5e. "
        "Ejecuta `python setup_rag.py` para inicializar la base de conocimientos."
    )

# Aviso si el agente no está disponible
if not _agent_available:
    st.error(
        f"❌ **Error al cargar el agente:** `{_agent_error}`\n\n"
        "Verifica que todos los módulos estén correctamente instalados."
    )
    st.stop()

st.markdown("---")

# ---------------------------------------------------------------------------
# MENSAJE DE BIENVENIDA — Se muestra automáticamente al iniciar
# ---------------------------------------------------------------------------

BIENVENIDA_HARDCODED = """¡Saludos, valiente aventurero! Soy tu **Dungeon Master**, 
guiado por la magia de la inteligencia artificial.

🏰 Te encuentras ante las puertas de una antigua mazmorra élfica, olvidada por los siglos. 
Las antorchas en las paredes parpadean con una luz tenue, proyectando sombras inquietantes. 
El aire huele a piedra húmeda y algo más... algo antiguo.

**Tu personaje:** Un aventurero curtido que busca gloria y fortuna en las profundidades de este lugar maldito.

¿Qué decides hacer? Puedes:
- 🗡️ **Explorar** el primer pasillo
- 🔦 **Inspeccionar** los alrededores
- 📖 **Preguntar** sobre el lugar al anciano que encontraste en la entrada
- ⚔️ **Cualquier otra acción** que se te ocurra

*El destino aguarda...*"""

if not st.session_state.messages:
    # Intentar generar bienvenida con el agente; si falla, usar la hardcodeada
    if _agent_available and not st.session_state.welcomed:
        with st.spinner("🔮 El Dungeon Master prepara la aventura..."):
            try:
                mensaje_inicio = (
                    "INICIO_DE_PARTIDA: Preséntate como el Dungeon Master de esta aventura de D&D 5e. "
                    "Describe una escena de apertura atmosférica y emocionante en una mazmorra o dungeon. "
                    "Pregunta qué hace el jugador para comenzar. Responde SOLO en español."
                )
                resultado = run_agent(mensaje_inicio, thread_id=st.session_state.thread_id)
                texto_bienvenida = texto_de(resultado.get("response", BIENVENIDA_HARDCODED))
                tool_calls_bienvenida = resultado.get("tool_calls", [])
            except Exception as e:
                texto_bienvenida = BIENVENIDA_HARDCODED
                tool_calls_bienvenida = []

        # Guardar bienvenida en el historial
        st.session_state.messages.append({
            "role": "assistant",
            "content": texto_bienvenida,
            "tool_calls": tool_calls_bienvenida
        })
        st.session_state.welcomed = True
    else:
        # Usar bienvenida hardcodeada
        st.session_state.messages.append({
            "role": "assistant",
            "content": BIENVENIDA_HARDCODED,
            "tool_calls": []
        })
        st.session_state.welcomed = True

# ---------------------------------------------------------------------------
# HISTORIAL DE MENSAJES
# ---------------------------------------------------------------------------

for mensaje in st.session_state.messages:
    rol = mensaje["role"]
    contenido = mensaje["content"]
    tool_calls = mensaje.get("tool_calls", [])

    with st.chat_message(rol, avatar="🎲" if rol == "assistant" else "🧙"):
        # Si hay tool calls, mostrarlos en expanders primero
        if tool_calls and rol == "assistant":
            renderizar_tool_calls(tool_calls)

        # Mostrar el contenido del mensaje
        st.markdown(texto_de(contenido))

# ---------------------------------------------------------------------------
# INPUT DEL USUARIO
# ---------------------------------------------------------------------------

if prompt := st.chat_input("¿Qué hace tu personaje?"):

    # 1. Agregar mensaje del usuario al historial y mostrarlo
    st.session_state.messages.append({
        "role": "user",
        "content": prompt,
        "tool_calls": []
    })

    with st.chat_message("user", avatar="🧙"):
        st.markdown(prompt)

    # 2. Llamar al agente y mostrar la respuesta
    with st.chat_message("assistant", avatar="🎲"):
        with st.spinner("🔮 El Dungeon Master consulta los arcanos..."):
            try:
                # Llamar al agente principal
                resultado = run_agent(prompt, thread_id=st.session_state.thread_id)
                respuesta = texto_de(resultado.get("response", "El Dungeon Master guarda silencio..."))
                tool_calls = resultado.get("tool_calls", [])

                # Mostrar tool calls en expanders ANTES de la respuesta final
                if tool_calls:
                    renderizar_tool_calls(tool_calls)

                # Mostrar la respuesta del DM
                st.markdown(respuesta)

                # Guardar en el historial
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": respuesta,
                    "tool_calls": tool_calls
                })

                # Verificar si se actualizó el mapa (herramienta switch_and_render_map)
                _mapa_actualizado = any(
                    "map" in tc.get("name", "").lower() or "render" in tc.get("name", "").lower()
                    for tc in tool_calls
                )

            except Exception as e:
                # Mostrar error amigable
                mensaje_error = (
                    f"⚔️ **El Dungeon Master tropieza con un hechizo roto...**\n\n"
                    f"Error técnico: `{str(e)}`\n\n"
                    "Intenta de nuevo o reinicia la partida con '🔄 Nueva Partida'."
                )
                st.error(mensaje_error)
                # Guardar el error en el historial para mantener contexto
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": f"*[Error del sistema: {str(e)}]*",
                    "tool_calls": []
                })

    # 3. Refrescar la página para actualizar el sidebar (HP, mapa, etc.)
    st.rerun()
