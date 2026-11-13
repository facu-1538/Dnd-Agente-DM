"""
app.py - Interfaz del Dungeon Master AI (Streamlit)

Chat con el agente D&D 5e. Muestra en vivo (streaming) qué hace el agente:
  1. su razonamiento (bloque diferenciado y plegable),
  2. cada herramienta invocada, en orden, con argumentos y resumen del resultado,
  3. la respuesta final del Dungeon Master.
Panel lateral con el estado del personaje y el mapa (exportable como PNG).

Estilo: el tema (colores, fuentes, radios) está en .streamlit/config.toml y solo la
tipografía de lectura de la narración está en ui/style.css.
"""

import json
import os
import re
import uuid
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# ---------------------------------------------------------------------------
# CONFIGURACIÓN DE PÁGINA (debe ser lo primero que se llama en Streamlit)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Dungeon Master AI",
    page_icon=":material/swords:",
    layout="centered",
    initial_sidebar_state="expanded",
)

# Tipografía de lectura de la narración (el resto del estilo vive en config.toml)
_css = Path(__file__).parent / "ui" / "style.css"
if _css.exists():
    st.html(f"<style>{_css.read_text(encoding='utf-8')}</style>")

# ---------------------------------------------------------------------------
# VERIFICACIÓN DE API KEY — Detenemos la app si no está configurada
# ---------------------------------------------------------------------------
if not os.getenv("GEMINI_API_KEY"):
    st.error(
        "Editá el archivo `.env` y agregá tu clave de API.",
        title="GEMINI_API_KEY no configurada",
        icon=":material/key:",
    )
    st.caption("Obtené una clave en https://aistudio.google.com/app/apikey")
    st.stop()

# ---------------------------------------------------------------------------
# IMPORTACIONES DE MÓDULOS PROPIOS (después de verificar la API key)
# ---------------------------------------------------------------------------
try:
    from game_engine.state_manager import get_state_manager
    _state_manager_available = True
except ImportError as e:
    _state_manager_available = False
    _state_manager_error = str(e)

try:
    from agent.core import run_agent, stream_agent, clear_conversation
    _agent_available = True
except ImportError as e:
    _agent_available = False
    _agent_error = str(e)

# Verificar si el vectorstore existe (RAG)
_vector_db_path = Path("storage/vector_db")
_vectorstore_available = _vector_db_path.exists() and any(_vector_db_path.iterdir())

MAPA_PATH = Path("storage/maps/current_view.png")

# ---------------------------------------------------------------------------
# SESSION STATE
# ---------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []          # historial visible de la partida

if "thread_id" not in st.session_state:
    st.session_state.thread_id = str(uuid.uuid4())   # hilo de memoria del agente

if "welcomed" not in st.session_state:
    st.session_state.welcomed = False

# ---------------------------------------------------------------------------
# PRESENTACIÓN DE HERRAMIENTAS
# ---------------------------------------------------------------------------

# nombre de la tool -> (icono Material con color, título legible)
TOOL_UI = {
    "query_rules_rag": (":blue[:material/menu_book:]", "Consulta de reglas"),
    "execute_dice_roll": (":orange[:material/casino:]", "Tirada de dados"),
    "switch_and_render_map": (":green[:material/map:]", "Mapa del dungeon"),
    "damage_player": (":red[:material/heart_broken:]", "Daño al jugador"),
    "heal_player": (":green[:material/healing:]", "Curación"),
}


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


def _json(salida: str):
    """Parsea la salida de una tool si es un JSON de objeto; si no, devuelve None."""
    try:
        datos = json.loads(salida)
        return datos if isinstance(datos, dict) else None
    except (TypeError, ValueError):
        return None


def es_error(salida: str) -> bool:
    """Heurística: las tools devuelven '❌'/'⚠️' o un JSON con clave 'error' al fallar."""
    if not salida:
        return False
    if salida.lstrip().startswith(("❌", "⚠️")):
        return True
    datos = _json(salida)
    return bool(datos and "error" in datos)


def resumen_tool(registro: dict) -> str:
    """Resumen de una línea del resultado de una tool (se muestra en el título del paso)."""
    nombre, salida = registro.get("name", ""), registro.get("output", "")
    if not salida:
        return ""
    datos = _json(salida)
    try:
        if nombre == "execute_dice_roll" and datos and "total" in datos:
            r = f"{datos.get('formula')} → {datos['total']}"
            if datos.get("success") is True:
                r += " · éxito"
            elif datos.get("success") is False:
                r += " · fallo"
            return r
        if nombre == "damage_player" and datos and "nuevo_hp" in datos:
            return f"−{datos.get('danio')} HP ({datos.get('hp_anterior')} → {datos['nuevo_hp']})"
        if nombre == "heal_player" and datos and "nuevo_hp" in datos:
            return f"+{datos.get('curacion')} HP ({datos.get('hp_anterior')} → {datos['nuevo_hp']})"
        if nombre == "query_rules_rag":
            fuentes = re.findall(r"^\[(.+?)\]", salida, flags=re.M)
            if fuentes:
                return f"{len(fuentes)} fragmentos · {fuentes[0]}"
        if datos and "error" in datos:
            return "error"
    except Exception:
        pass
    primera = next((l for l in salida.splitlines() if l.strip()), "")
    # Quita emojis/símbolos iniciales: se conserva desde la primera letra o dígito latino
    primera = re.sub(r"^[^A-Za-zÀ-ÿ0-9]+", "", primera.replace("*", ""))
    return primera.strip()[:80]


def etiqueta_tool(registro: dict) -> str:
    """Título del paso: icono de color, nombre legible y resumen del resultado."""
    nombre = registro.get("name", "tool")
    icono, titulo = TOOL_UI.get(nombre, (":gray[:material/build:]", nombre))
    resumen = resumen_tool(registro)
    return f"{icono} **{titulo}**" + (f" · {resumen}" if resumen else "")


def estado_tool(registro: dict) -> str:
    salida = registro.get("output", "")
    if not salida:
        return "running"
    return "error" if es_error(salida) else "complete"


def mostrar_args(entrada: dict):
    """Argumentos con los que se invocó la tool."""
    if entrada:
        st.caption("Argumentos")
        st.code(json.dumps(entrada, ensure_ascii=False, indent=2), language="json")


def mostrar_resultado(registro: dict):
    """Resultado de la tool, con formato propio para las tiradas de dados."""
    nombre, salida = registro.get("name", ""), registro.get("output", "")
    if not salida:
        return
    st.caption("Resultado")
    datos = _json(salida)
    if nombre == "execute_dice_roll" and datos and "total" in datos:
        mod = datos.get("modifier", 0)
        linea = f"Dados `{datos.get('rolls')}` · modificador `{mod:+d}` · total **{datos['total']}**"
        if datos.get("target"):
            veredicto = ":green[éxito]" if datos.get("success") else ":red[fallo]"
            linea += f" · contra {datos['target']}: {veredicto}"
        st.markdown(linea)
        if datos.get("reason"):
            st.caption(datos["reason"])
    elif datos is not None:
        st.code(json.dumps(datos, ensure_ascii=False, indent=2), language="json")
    else:
        # Texto de RAG o del mapa: acotado para no ocupar toda la pantalla
        texto = salida if len(salida) <= 700 else salida[:700] + "…"
        st.code(texto, language=None, wrap_lines=True)


def paso_estatico(registro: dict):
    """Paso de herramienta ya terminado (historial)."""
    with st.status(etiqueta_tool(registro), type="step", state=estado_tool(registro), expanded=False):
        mostrar_args(registro.get("input", {}))
        mostrar_resultado(registro)


def renderizar_razonamiento(texto: str, key: str):
    """Razonamiento del modelo (historial): plegado y diferenciado de la respuesta."""
    with st.expander(":violet[:material/psychology:] Razonamiento", type="compact"):
        with st.container(key=key):      # key "razon_*": estilo apagado en ui/style.css
            st.markdown(texto)


def uso_mapa(tool_calls: list) -> bool:
    """True si en estas tool calls el agente actualizó el mapa."""
    return any(tc.get("name") == "switch_and_render_map" for tc in tool_calls)


def boton_descarga_mapa(key: str):
    """Botón para exportar el mapa actual (PNG). `key` debe ser único en la página."""
    if MAPA_PATH.exists():
        st.download_button(
            "Descargar mapa",
            data=MAPA_PATH.read_bytes(),
            file_name="mapa_dungeon.png",
            mime="image/png",
            icon=":material/download:",
            key=key,
        )


def nueva_partida():
    """Reinicia el estado del juego, la memoria del agente y el historial visible."""
    if _agent_available:
        try:
            clear_conversation(st.session_state.thread_id)   # libera el checkpointer
        except Exception:
            pass

    st.session_state.messages = []
    st.session_state.welcomed = False
    st.session_state.thread_id = str(uuid.uuid4())

    if _state_manager_available:
        try:
            get_state_manager().reset_state()
        except Exception as e:
            st.toast(f"No se pudo reiniciar el estado: {e}", icon=":material/error:")
    # No hace falta st.rerun(): se usa como callback on_click y Streamlit re-ejecuta solo.


# ---------------------------------------------------------------------------
# SIDEBAR — Personaje y mapa (compacto: debe verse completo sin scroll)
# ---------------------------------------------------------------------------
with st.sidebar:
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
            st.markdown(f"#### :orange[:material/shield_person:] {info.get('nombre', 'Aventurero')}")
            st.caption(f"{info.get('clase', '?')} · Nivel {info.get('nivel', '?')}")

            st.markdown(f":red[:material/favorite:] **Vida** &nbsp; {hp} / {max_hp}")
            st.progress(max(0.0, min(1.0, hp / max_hp)))

            col_oro, col_xp = st.columns(2)
            col_oro.markdown(f":orange[:material/paid:] **{info.get('oro', 0)}** oro")
            col_xp.markdown(f":violet[:material/auto_awesome:] **{info.get('experiencia', 0)}** XP")

        # Enemigos: solo ocupan lugar si hay combate
        try:
            npcs = state_manager.get_active_npcs()
        except Exception:
            npcs = {}
        if npcs:
            st.markdown("#### :red[:material/skull:] Enemigos")
            for nombre_npc, datos_npc in npcs.items():
                hp_npc = datos_npc.get("hp", "?")
                max_hp_npc = datos_npc.get("max_hp", hp_npc)
                st.markdown(f"**{nombre_npc}** · {hp_npc}/{max_hp_npc}")
                if isinstance(hp_npc, (int, float)) and isinstance(max_hp_npc, (int, float)) and max_hp_npc > 0:
                    st.progress(max(0.0, min(1.0, hp_npc / max_hp_npc)))
        else:
            st.caption(":gray[:material/shield:] Sin enemigos en combate")
    else:
        st.warning(f"Motor de juego no disponible: `{_state_manager_error}`")

    # --- Mapa del dungeon (producto tangible de la partida) ---
    st.markdown("#### :green[:material/map:] Mapa")
    if MAPA_PATH.exists():
        st.image(str(MAPA_PATH), width="stretch", alt="Mapa del dungeon con las salas exploradas")
        boton_descarga_mapa("descargar_mapa")
    else:
        with st.container(border=True):
            st.caption("El mapa se revelará al explorar el dungeon.")

    st.button("Nueva partida", icon=":material/restart_alt:", width="stretch", on_click=nueva_partida)

    st.caption(
        f"RAG {'activo' if _vectorstore_available else 'sin inicializar'} · "
        f"agente {'ok' if _agent_available else 'con error'} · "
        f"hilo `{st.session_state.thread_id[:8]}`"
    )

# ---------------------------------------------------------------------------
# PANEL PRINCIPAL
# ---------------------------------------------------------------------------
st.title("Dungeon Master", icon=":material/swords:")
st.caption("Agente de D&D 5e con RAG y herramientas · Gemini + LangChain + LangGraph")

if not _vectorstore_available:
    st.warning(
        "El DM funcionará sin consultar el reglamento de D&D 5e. "
        "Ejecutá `python setup_rag.py` para inicializar la base de conocimientos.",
        title="Base de conocimientos RAG no inicializada",
        icon=":material/database:",
    )

if not _agent_available:
    st.error(
        f"`{_agent_error}`\n\nVerificá que todas las dependencias estén instaladas.",
        title="Error al cargar el agente",
        icon=":material/error:",
    )
    st.stop()

# ---------------------------------------------------------------------------
# MENSAJE DE BIENVENIDA
# ---------------------------------------------------------------------------
BIENVENIDA_HARDCODED = """Saludos, valiente aventurero. Soy tu **Dungeon Master**.

Te encontrás ante las puertas de una antigua mazmorra élfica, olvidada por los siglos.
Las antorchas parpadean con una luz tenue y el aire huele a piedra húmeda y a algo más... algo antiguo.

¿Qué decidís hacer?

- **Explorar** el primer pasillo
- **Inspeccionar** los alrededores
- **Preguntar** al anciano que espera en la entrada

*El destino aguarda.*"""

if not st.session_state.messages:
    if not st.session_state.welcomed:
        texto_bienvenida, tool_calls_bienvenida = BIENVENIDA_HARDCODED, []
        with st.spinner("El Dungeon Master prepara la aventura…"):
            mensaje_inicio = (
                "INICIO_DE_PARTIDA: Preséntate como el Dungeon Master de esta aventura de D&D 5e. "
                "Describe una escena de apertura atmosférica y emocionante en una mazmorra o dungeon. "
                "Pregunta qué hace el jugador para comenzar. Responde SOLO en español."
            )
            resultado = run_agent(mensaje_inicio, thread_id=st.session_state.thread_id)
            respuesta = texto_de(resultado.get("response", ""))
            # run_agent no lanza excepciones: si falló, devuelve un texto que empieza con ❌
            if respuesta and not respuesta.startswith("❌"):
                texto_bienvenida = respuesta
                tool_calls_bienvenida = resultado.get("tool_calls", [])

        st.session_state.messages.append({
            "role": "assistant",
            "content": texto_bienvenida,
            "tool_calls": tool_calls_bienvenida,
        })
    else:
        st.session_state.messages.append({
            "role": "assistant", "content": BIENVENIDA_HARDCODED, "tool_calls": [],
        })
    st.session_state.welcomed = True

# ---------------------------------------------------------------------------
# HISTORIAL DE MENSAJES
# ---------------------------------------------------------------------------
AVATAR = {"assistant": ":material/auto_stories:", "user": ":material/person:"}

for idx, mensaje in enumerate(st.session_state.messages):
    rol = mensaje["role"]
    tool_calls = mensaje.get("tool_calls", [])

    with st.chat_message(rol, avatar=AVATAR.get(rol)):
        if rol == "assistant":
            # Orden fijo: razonamiento, herramientas (en orden de invocación), respuesta
            if mensaje.get("reasoning"):
                renderizar_razonamiento(mensaje["reasoning"], key=f"razon_{idx}")
            for registro in tool_calls:
                paso_estatico(registro)
            with st.container(key=f"narr_{idx}"):
                st.markdown(texto_de(mensaje["content"]))
            if uso_mapa(tool_calls):
                boton_descarga_mapa(f"dl_mapa_{idx}")
        else:
            st.markdown(texto_de(mensaje["content"]))

# ---------------------------------------------------------------------------
# INPUT DEL USUARIO + STREAMING DEL AGENTE
# ---------------------------------------------------------------------------
# submit_mode="disable": mientras el agente trabaja no se puede enviar otro mensaje
if prompt := st.chat_input("¿Qué hace tu personaje?", submit_mode="disable"):

    st.session_state.messages.append({"role": "user", "content": prompt, "tool_calls": []})
    with st.chat_message("user", avatar=AVATAR["user"]):
        st.markdown(prompt)

    # Se reservan los tres espacios (razonamiento, tools, respuesta) y se llenan en vivo
    with st.chat_message("assistant", avatar=AVATAR["assistant"]):
        razon_box = st.container()
        tools_box = st.container()
        with st.container(key="narr_live"):
            texto_box = st.empty()
        texto_box.markdown(":shimmer[El Dungeon Master consulta los arcanos…]")

        razonamiento, texto, error = "", "", None
        tool_calls: list = []
        pasos: dict = {}        # id de tool_call -> (registro, st.status del paso)
        razon_estado = None     # st.status del razonamiento; se crea con el primer bloque
        razon_texto = None

        for ev in stream_agent(prompt, thread_id=st.session_state.thread_id):
            tipo = ev["type"]

            if tipo == "reasoning":
                razonamiento += ev["text"]
                if razon_estado is None:
                    with razon_box:
                        razon_estado = st.status(":shimmer[Razonando…]", type="compact", expanded=True)
                        with razon_estado:
                            with st.container(key="razon_live"):
                                razon_texto = st.empty()
                razon_texto.markdown(razonamiento)

            elif tipo == "tool_call":
                registro = {"name": ev["name"], "input": ev["args"], "output": ""}
                tool_calls.append(registro)
                # Separar el texto previo a la tool del que venga después
                if texto and not texto.endswith("\n\n"):
                    texto += "\n\n"
                # El paso aparece apenas el agente decide invocar la tool, ya con sus argumentos
                with tools_box:
                    estado = st.status(etiqueta_tool(registro), type="step", state="running")
                    with estado:
                        mostrar_args(registro["input"])
                pasos[ev["id"]] = (registro, estado)

            elif tipo == "tool_result":
                if ev["id"] in pasos:
                    registro, estado = pasos[ev["id"]]
                    registro["output"] = ev["output"]
                    with estado:
                        mostrar_resultado(registro)
                    estado.update(label=etiqueta_tool(registro), state=estado_tool(registro))

            elif tipo == "text":
                texto += ev["text"]
                texto_box.markdown(texto + "▌")

            elif tipo == "error":
                error = ev["message"]

        if razon_estado is not None:
            razon_estado.update(
                label=":violet[:material/psychology:] Razonamiento", state="complete", expanded=False
            )
        texto_box.markdown(texto)

        # Exportar el mapa desde el chat si este turno lo actualizó (misma key que usará el historial)
        if uso_mapa(tool_calls):
            boton_descarga_mapa(f"dl_mapa_{len(st.session_state.messages)}")

        if error:
            st.error(
                f"`{error}`\n\nIntentá de nuevo o reiniciá con «Nueva partida».",
                title="El Dungeon Master tropezó con un hechizo roto",
                icon=":material/error:",
            )

        # Guardar en el historial (con razonamiento y tools para poder re-renderizar)
        st.session_state.messages.append({
            "role": "assistant",
            "content": texto or (f"*[Error del sistema: {error}]*" if error else ""),
            "tool_calls": tool_calls,
            "reasoning": razonamiento,
        })

    # Refrescar para actualizar el sidebar (HP, mapa, etc.)
    st.rerun()
