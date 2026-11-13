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

import html
import json
import unicodedata
import os
import re
import time
from datetime import datetime
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

# Marca en la cabecera de la barra lateral (como ChatGPT o Claude): ocupa la franja superior
# que Streamlit reserva siempre, y con la barra colapsada se muestra solo el ícono.
_logo, _logo_icono = Path("assets/logo.png"), Path("assets/logo_icono.png")
if _logo.exists():
    st.logo(str(_logo), size="large", icon_image=str(_logo_icono) if _logo_icono.exists() else None)

# Tipografía de lectura de la narración (el resto del estilo vive en config.toml)
_css = Path(__file__).parent / "ui" / "style.css"
if _css.exists():
    st.html(f"<style>{_css.read_text(encoding='utf-8')}</style>")

# Salto del riel de mensajes: script fijo (sin datos del usuario), se instala una sola vez.
# El hash (#msg-N) solo no alcanza porque el contenedor del chat mantiene el scroll abajo.
st.html(
    """<script>
    if (!window.__rielListo) {
      window.__rielListo = true;
      document.addEventListener("click", (e) => {
        const a = e.target.closest && e.target.closest(".riel a");
        if (!a) return;
        e.preventDefault();
        const destino = document.getElementById(a.getAttribute("href").slice(1));
        if (destino) destino.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    }
    </script>""",
    unsafe_allow_javascript=True,
)

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
    from game_engine.map_renderer import DUNGEON_LAYOUT, get_map_renderer
except ImportError:
    DUNGEON_LAYOUT, get_map_renderer = {}, None

try:
    from ui.visor_mapa import visor_mapa      # mapa con zoom de rueda (componente v2)
except Exception:                              # si falla, el diálogo muestra una imagen simple
    visor_mapa = None

try:
    from agent.core import stream_agent, clear_conversation, seed_conversation, get_conversation_length
    _agent_available = True
except ImportError as e:
    _agent_available = False
    _agent_error = str(e)

# Verificar si el vectorstore existe (RAG)
_vector_db_path = Path("storage/vector_db")
_vectorstore_available = _vector_db_path.exists() and any(_vector_db_path.iterdir())



def ruta_mapa() -> Path:
    """Mapa de la partida activa (cada partida guarda el suyo en su carpeta)."""
    return get_state_manager().ruta_mapa if _state_manager_available else Path("storage/maps/current_view.png")


# ---------------------------------------------------------------------------
# PARTIDA ACTIVA DE ESTA SESIÓN
# ---------------------------------------------------------------------------
# Cada partida guardada tiene su carpeta (estado, historial, mapa, informes) y su memoria del agente
# (SQLite), y UN solo id (el de la carpeta) gobierna los tres. El estado del juego es un singleton del
# PROCESO, así que al inicio de cada ejecución se lo reapunta a la partida de ESTA sesión.
if not _state_manager_available:
    st.error(f"`{_state_manager_error}`", title="Error al cargar el motor de juego", icon=":material/error:")
    st.stop()

_sm = get_state_manager()
_sm.validar_activa()                                     # por si la carpeta activa desapareció
if "partida_id" not in st.session_state:
    st.session_state.partida_id = _sm.id_activa          # la última activa: recargar la página la retoma
elif not _sm.abrir_partida(st.session_state.partida_id):
    st.session_state.partida_id = _sm.id_activa          # la borraron desde otra pestaña

# El historial visible se carga de disco al entrar a una partida (y al cambiar de partida)
if st.session_state.get("mensajes_de") != st.session_state.partida_id:
    st.session_state.messages = _sm.cargar_mensajes()
    st.session_state.mensajes_de = st.session_state.partida_id

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
    "set_character": (":orange[:material/badge:]", "Ficha del personaje"),
    "manage_enemy": (":red[:material/skull:]", "Enemigo"),
    "generate_adventure_report": (":violet[:material/picture_as_pdf:]", "Crónica de la aventura"),
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
    # Prefijos de error de las tools, incluidos los mensajes de fallo del retriever del RAG
    if salida.lstrip().startswith(
        ("❌", "⚠️", "Error al realizar", "No hay base de conocimientos")
    ):
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
            # Encabezados "[Fuente 1: monsters.txt › Goblin]": se resume el primero
            fuentes = re.findall(r"^\[Fuente \d+: (.+?)\]", salida, flags=re.M)
            if fuentes:
                return f"{len(fuentes)} fragmento{'s' if len(fuentes) != 1 else ''} · {fuentes[0]}"
        if nombre == "set_character" and datos and "nombre" in datos:
            return f"{datos['nombre']} · {datos.get('raza', '')} {datos.get('clase', '')}".strip()
        if nombre == "manage_enemy" and datos and "nombre" in datos:
            if datos.get("accion") == "spawn":
                return f"entra {datos['nombre']} ({datos.get('hp')} HP)"
            if datos.get("eliminado"):
                rec = datos.get("recompensa", {})
                return f"{datos['nombre']} derrotado · +{rec.get('xp', 0)} XP, +{rec.get('oro', 0)} oro"
            if datos.get("accion") == "damage":
                return f"{datos['nombre']}: {datos.get('hp_anterior')} → {datos.get('nuevo_hp')} HP"
            return f"{datos['nombre']} abandona el combate"
        if nombre == "generate_adventure_report" and datos and "pdf" in datos:
            return f"PDF listo · {datos.get('paginas')} páginas · {datos.get('kb')} KB"
        if nombre == "switch_and_render_map":
            m = re.search(r"^Ubicación actual: (.+?) \(", salida, flags=re.M)
            if m:
                return f"ahora en {m.group(1)}"
        if datos and "error" in datos:
            return "error"
    except Exception:
        pass
    primera = next((l for l in salida.splitlines() if l.strip()), "")
    # Quita emojis/símbolos iniciales: se conserva desde la primera letra o dígito latino
    primera = re.sub(r"^[^A-Za-zÀ-ÿ0-9]+", "", primera.replace("*", ""))
    return primera.strip()[:80]


def pista_args(entrada: dict) -> str:
    """Pista corta de los argumentos, para el título del paso mientras la tool aún corre."""
    if not entrada:
        return ""
    pista = ", ".join(f"{k}={v}" for k, v in list(entrada.items())[:2])
    return pista if len(pista) <= 70 else pista[:69] + "…"


def etiqueta_tool(registro: dict) -> str:
    """
    Título del paso: icono de color, nombre legible y, mientras la tool corre, una pista
    de sus argumentos; al terminar, el resumen del resultado.
    """
    nombre = registro.get("name", "tool")
    icono, titulo = TOOL_UI.get(nombre, (":gray[:material/build:]", nombre))
    detalle = resumen_tool(registro) or pista_args(registro.get("input", {}))
    # Tiempo desde que el agente anunció la tool hasta su resultado (las tools que corren en
    # paralelo comparten tiempo; las instantáneas, como los dados, no muestran nada)
    seg = registro.get("seg")
    tiempo = f" · {seg:.1f} s" if seg is not None and seg >= 0.5 else ""
    return f"{icono} **{titulo}**" + (f" · {detalle}" if detalle else "") + tiempo


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
    elif nombre == "query_rules_rag":
        # Primero, de qué documentos salió la información (es lo que el DM debe citar)
        m = re.search(r"^FUENTES CONSULTADAS: (.+)$", salida, flags=re.M)
        if m:
            st.markdown("**Fuentes consultadas**")
            for fuente in m.group(1).split(";"):
                st.markdown(f"- :blue[:material/description:] `{fuente.strip()}`")
        cuerpo = salida.split("\n\nFUENTES CONSULTADAS:")[0]
        st.code(cuerpo if len(cuerpo) <= 700 else cuerpo[:700] + "…", language=None, wrap_lines=True)
    elif datos is not None:
        st.code(json.dumps(datos, ensure_ascii=False, indent=2), language="json")
    else:
        # Texto de RAG o del mapa: acotado para no ocupar toda la pantalla
        texto = salida if len(salida) <= 700 else salida[:700] + "…"
        st.code(texto, language=None, wrap_lines=True)


def html_dado(registro: dict) -> str:
    """
    Tarjeta de una tirada: un d20 que gira, el número grande y, si había dificultad, el veredicto.
    Devuelve "" si el registro no es una tirada válida.
    """
    datos = _json(registro.get("output", "")) if registro.get("name") == "execute_dice_roll" else None
    if not datos or "total" not in datos:
        return ""
    total, mod = datos["total"], datos.get("modifier", 0)
    tiradas = datos.get("rolls") or []
    objetivo, exito = datos.get("target"), datos.get("success")
    clase = "neutral" if objetivo is None or exito is None else ("ok" if exito else "fail")
    etiqueta = {"ok": "ÉXITO", "fail": "FALLO"}.get(clase, "")
    # Natural 20 / natural 1: solo cuando es una tirada de un único d20
    especial = ""
    if len(tiradas) == 1 and "d20" in str(datos.get("formula", "")).lower():
        if tiradas[0] == 20:
            especial = '<span class="dado-especial">¡CRÍTICO!</span>'
        elif tiradas[0] == 1:
            especial = '<span class="dado-especial">¡PIFIA!</span>'
    calculo = " + ".join(str(t) for t in tiradas) if tiradas else str(total)
    if mod:
        calculo += f" {'+' if mod > 0 else '−'} {abs(mod)}"
    calculo += f" = {total}"
    contra = f" · contra {html.escape(str(objetivo))}" if objetivo is not None else ""
    razon = html.escape(str(datos.get("reason") or "Tirada de dados"))
    if etiqueta or especial:
        veredicto = f'<div class="dado-res">{etiqueta}{especial}</div>'
    else:
        veredicto = ""
    return (
        f'<div class="dado-card {clase}"><div class="dado">'
        '<div class="giro"><i class="hex"></i><i class="hex2"></i><i class="tri"></i><i class="tri2"></i></div>'
        f'<span class="n">{html.escape(str(total))}</span></div>'
        f'<div class="dado-info"><div class="dado-razon">{razon}</div>'
        f'<div class="dado-calc">{html.escape(str(datos.get("formula", "")))} · {calculo}{contra}</div></div>'
        f'{veredicto}</div>'
    )


def mostrar_dado(registro: dict):
    tarjeta = html_dado(registro)
    if tarjeta:
        st.html(tarjeta)


def paso_estatico(registro: dict, abierto: bool):
    """Paso de herramienta ya terminado (historial)."""
    with st.status(etiqueta_tool(registro), type="step", state=estado_tool(registro), expanded=abierto):
        mostrar_args(registro.get("input", {}))
        mostrar_resultado(registro)
    mostrar_dado(registro)


def renderizar_razonamiento(texto: str, key: str, abierto: bool):
    """Razonamiento del modelo (historial): diferenciado de la respuesta; abierto o plegado."""
    with st.expander(":violet[:material/psychology:] Razonamiento", expanded=abierto, type="compact"):
        with st.container(key=key):      # key "razon_*": estilo apagado en ui/style.css
            st.markdown(texto)


def pdf_generado(tool_calls: list):
    """Ruta del PDF si en estas tool calls el agente generó la crónica (y el archivo existe)."""
    for tc in tool_calls:
        if tc.get("name") == "generate_adventure_report":
            datos = _json(tc.get("output", ""))
            if datos and datos.get("pdf") and Path(datos["pdf"]).exists():
                return Path(datos["pdf"])
    return None


def boton_descarga_pdf(ruta: Path, key: str):
    """Descarga el PDF que produjo la tool del agente (la UI no genera nada por su cuenta)."""
    st.download_button(
        "Descargar crónica (PDF)",
        data=ruta.read_bytes(),
        file_name=ruta.name,
        mime="application/pdf",
        icon=":material/picture_as_pdf:",
        type="primary",
        key=key,
    )


def resumen_turno(n_tools: int, seg: float) -> str:
    """Línea de cierre de un turno: cuántas herramientas usó el agente y cuánto tardó en total."""
    herramientas = "sin herramientas" if n_tools == 0 else f"{n_tools} herramienta{'s' if n_tools != 1 else ''}"
    return f":gray[:material/timer:] {herramientas} · {seg:.1f} s"


def uso_mapa(tool_calls: list) -> bool:
    """True si en estas tool calls el agente actualizó el mapa."""
    return any(tc.get("name") == "switch_and_render_map" for tc in tool_calls)


def boton_descarga_mapa(key: str, etiqueta: str = "Descargar mapa", ancho: str = "content"):
    """Botón para exportar el mapa actual (PNG). `key` debe ser único en la página."""
    if ruta_mapa().exists():
        st.download_button(
            etiqueta,
            data=ruta_mapa().read_bytes(),
            file_name="mapa_dungeon.png",
            mime="image/png",
            icon=":material/download:",
            width=ancho,
            key=key,
        )


@st.dialog("Mapa de la mazmorra", width="large")
def ver_mapa_grande():
    """El mapa en grande, centrado y sin scroll, con zoom de rueda (ver ui/visor_mapa.py)."""
    if not ruta_mapa().exists():
        st.caption("Todavía no hay mapa.")
    elif visor_mapa is not None:
        visor_mapa(ruta_mapa(), key="visor_mapa")
    else:                                          # respaldo si el componente no se pudo cargar
        st.image(str(ruta_mapa()), width="stretch", alt="Mapa ilustrado de la mazmorra con las salas exploradas")


# Aspecto de la ficha: emoji de la raza y ícono (Material) de la clase, cada uno con su color
_RAZAS = {"humano": ("person", "#e3a73b"), "elfo": ("nature", "#5fd08a"), "enano": ("hardware", "#d98b4a"),
          "mediano": ("cottage", "#8fc96b"), "semielfo": ("nature_people", "#7fd6b0"),
          "semiorco": ("fitness_center", "#9bb36a"), "tiefling": ("whatshot", "#d6577a"),
          "draconido": ("air", "#e0834d"), "gnomo": ("construction", "#c58be0")}
_CLASES = {"guerrero": ("swords", "#e0584d"), "mago": ("auto_fix_high", "#8b7bff"),
           "picaro": ("visibility_off", "#4fc3d9"), "clerigo": ("health_and_safety", "#f0d98a"),
           "barbaro": ("local_fire_department", "#f0883e"), "paladin": ("shield", "#e8c04a"),
           "explorador": ("forest", "#6fcf97"), "guardabosques": ("forest", "#6fcf97"),
           "bardo": ("music_note", "#e58bd0"), "druida": ("eco", "#7ed67e"), "brujo": ("dark_mode", "#a56bd9"),
           "hechicero": ("bolt", "#f2c94c"), "monje": ("self_improvement", "#8fd0f0")}


def _clave(texto: str) -> str:
    """Minúsculas y sin tildes, para buscar la raza/clase en las tablas de arriba."""
    return unicodedata.normalize("NFD", str(texto).lower()).encode("ascii", "ignore").decode().strip()


def html_ficha(info: dict) -> str:
    """Tarjeta del personaje: nombre grande, raza y clase con ícono y color, y el nivel en una insignia."""
    nombre = html.escape(str(info.get("nombre") or "Aventurero"))
    if not info.get("ficha_creada"):
        return (f'<div class="ficha"><div class="ficha-cuerpo"><div class="ficha-nombre">{nombre}</div>'
                '<div class="ficha-vacia">Personaje sin crear</div></div></div>')
    raza, clase = str(info.get("raza") or "").strip(), str(info.get("clase") or "").strip()
    icono_raza, c_raza = _RAZAS.get(_clave(raza), ("person", "#b9b6cc"))
    icono, c_clase = _CLASES.get(_clave(clase), ("shield_person", "#b9b6cc"))
    chips = ""
    if raza:
        chips += f'<span class="chip" style="--c:{c_raza}"><span class="ms">{icono_raza}</span>{html.escape(raza.capitalize())}</span>'
    if clase:
        chips += f'<span class="chip" style="--c:{c_clase}"><span class="ms">{icono}</span>{html.escape(clase.capitalize())}</span>'
    nivel = html.escape(str(info.get("nivel", 1)))
    return (f'<div class="ficha"><div class="ficha-cuerpo"><div class="ficha-nombre">{nombre}</div>'
            f'<div class="ficha-chips">{chips}</div></div>'
            f'<div class="nivel" title="Nivel {nivel}"><b>{nivel}</b><small>NIVEL</small></div></div>')


def hace_tiempo(cuando: datetime) -> str:
    """'recién', 'hace 5 min', 'hace 3 h', 'ayer' o la fecha."""
    seg = (datetime.now() - cuando).total_seconds()
    if seg < 60:
        return "recién"
    if seg < 3600:
        return f"hace {int(seg // 60)} min"
    if seg < 86400:
        return f"hace {int(seg // 3600)} h"
    if seg < 172800:
        return "ayer"
    return cuando.strftime("%d/%m")


def titulo_partida(p: dict) -> str:
    """Título de una partida guardada: el nombre del personaje (o 'Nueva aventura' si aún no lo creó)."""
    return p["nombre"] if p["ficha_creada"] else "Nueva aventura"


def detalle_partida(p: dict) -> str:
    base = f"{p['raza']} {p['clase']} · Nv {p['nivel']}".strip() if p["ficha_creada"] else "Sin personaje"
    return f"{base} · {p['turnos']} turno{'s' if p['turnos'] != 1 else ''} · {hace_tiempo(p['actualizado'])}"


def guardar_historial():
    """Guarda el chat visible de la partida activa (se llama tras cada mensaje del jugador y del DM)."""
    try:
        _sm.guardar_mensajes(st.session_state.messages)
    except OSError as e:
        st.toast(f"No se pudo guardar el historial: {e}", icon=":material/error:")


TAB_PARTIDA, TAB_HISTORIAL = ":material/shield_person: Partida", ":material/history: Historial"


def cambiar_a(partida_id: str):
    """Abre una partida guardada (callback del historial). El chat se carga solo en la ejecución siguiente."""
    if _sm.abrir_partida(partida_id):
        st.session_state.partida_id = partida_id
        st.session_state["ir_a_partida"] = True           # se pasa a "Partida" para ver sus stats


def nueva_partida():
    """Crea una partida nueva SIN tocar las anteriores (que quedan en el historial)."""
    actual = next((p for p in _sm.listar_partidas() if p["id"] == st.session_state.partida_id), None)
    if actual and not actual["ficha_creada"] and actual["turnos"] == 0:
        st.toast("Ya estás en una partida nueva: todavía no jugaste nada.", icon=":material/info:")
        return
    st.session_state.partida_id = _sm.nueva_partida()


@st.dialog("¿Borrar esta partida?")
def confirmar_borrado(partida_id: str, titulo: str):
    """Confirmación antes de borrar: es lo único irreversible de la interfaz."""
    st.markdown(f"Vas a borrar **{titulo}**: su ficha, el mapa, las crónicas y todo el chat.")
    st.caption("Esta acción no se puede deshacer.")
    col_cancelar, col_borrar = st.columns(2)
    if col_cancelar.button("Cancelar", width="stretch"):
        st.rerun()
    if col_borrar.button("Borrar", icon=":material/delete:", type="primary", width="stretch"):
        try:
            clear_conversation(partida_id)               # la memoria del agente en disco
        except Exception:
            pass
        activa = _sm.borrar_partida(partida_id)
        if st.session_state.partida_id == partida_id:
            st.session_state.partida_id = activa
        st.rerun()


# ---------------------------------------------------------------------------
# SIDEBAR — Nueva partida arriba y dos pestañas: la partida actual y el historial
# ---------------------------------------------------------------------------
# El mapa de una partida recién creada todavía no existe: se dibuja la niebla inicial
if not ruta_mapa().exists() and get_map_renderer is not None:
    try:
        get_map_renderer().render_fog_of_war(_sm.get_discovered_rooms())
    except Exception:
        pass

with st.sidebar:
    st.button("Nueva partida", icon=":material/add:", width="stretch", key="nueva_partida", on_click=nueva_partida)
    tab_partida, tab_historial = st.tabs([TAB_PARTIDA, TAB_HISTORIAL])
    if st.session_state.pop("ir_a_partida", False):
        # Streamlit no permite elegir la pestaña desde Python sin recargar la app (y recargar cortaría un turno
        # en curso): un script fijo hace el clic en la primera pestaña, una sola vez.
        st.html("""<script>setTimeout(() => {
            const t = document.querySelector('[data-testid="stSidebar"] [role="tab"]');
            if (t) t.click();
        }, 60);</script>""", unsafe_allow_javascript=True)

    # --- Pestaña "Partida": ficha, vida, ubicación, enemigos y mapa (compacto, sin scroll) ---
    with tab_partida:
        try:
            hp = _sm.get_player_hp()
            max_hp = _sm.get_player_max_hp()
            info = _sm.get_player_info()
        except Exception as e:
            st.warning(f"Error al cargar el estado: {e}")
            hp, max_hp, info = None, None, {}

        if hp is not None and max_hp:
            st.html(html_ficha(info))

            with st.container(key="vida"):
                st.markdown(f":red[:material/favorite:] **Vida** &nbsp; {hp} / {max_hp}")
                st.progress(max(0.0, min(1.0, hp / max_hp)))

            col_oro, col_xp = st.columns(2)
            col_oro.markdown(f":orange[:material/paid:] **{info.get('oro', 0)}** oro")
            col_xp.markdown(f":violet[:material/auto_awesome:] **{info.get('experiencia', 0)}** XP")

            try:
                sala = _sm.get_current_room()
                st.markdown(
                    f":green[:material/location_on:] **Ubicación** &nbsp; {DUNGEON_LAYOUT.get(sala, {}).get('nombre', sala)}"
                )
            except Exception:
                pass

        # Enemigos: solo ocupan lugar si hay combate
        try:
            npcs = _sm.get_active_npcs()
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

        # --- Mapa del dungeon (producto tangible de la partida) ---
        st.markdown("#### :green[:material/map:] Mapa")
        if ruta_mapa().exists():
            # El mapa entero es el botón: un clic lo abre en grande (con zoom y descarga adentro)
            with st.container(key="mapa_click"):
                st.image(str(ruta_mapa()), width="stretch", alt="Mapa del dungeon con las salas exploradas")
                if st.button(" ", key="ampliar_mapa"):
                    ver_mapa_grande()
        else:
            with st.container(border=True):
                st.caption("El mapa se revelará al explorar el dungeon.")

        # Pasos abiertos: despliega el razonamiento y cada herramienta (útil para la demo y la revisión)
        pasos_abiertos = st.toggle(
            "Dejar los pasos abiertos", value=False, key="pasos_abiertos",
            help="Mientras el agente responde, el razonamiento y cada herramienta se ven desplegados. "
                 "Al terminar se cierran (podés abrirlos con un clic); con esto activo quedan abiertos.",
        )

    # --- Pestaña "Historial": partidas guardadas y ajustes ---
    with tab_historial:
        partidas = _sm.listar_partidas()
        st.caption(f"{len(partidas)} partida{'s' if len(partidas) != 1 else ''} guardada{'s' if len(partidas) != 1 else ''}")
        # Alto fijo con scroll propio: la lista puede crecer sin empujar el resto de la barra
        with st.container(height=300, border=False):
            for p in partidas[:50]:
                es_actual = p["id"] == st.session_state.partida_id
                titulo = titulo_partida(p)
                col_abrir, col_borrar = st.columns([5, 1], vertical_alignment="center")
                with col_abrir:
                    st.button(
                        titulo if len(titulo) <= 24 else titulo[:23] + "…",
                        key=f"abrir_{p['id']}", icon=":material/chat:" if es_actual else ":material/chat_bubble_outline:",
                        type="primary" if es_actual else "secondary", width="stretch",
                        disabled=es_actual, on_click=cambiar_a, args=(p["id"],), help=f"{titulo} · {detalle_partida(p)}",
                    )
                with col_borrar:
                    if st.button(":material/delete:", key=f"borrar_{p['id']}", help="Borrar esta partida", width="stretch"):
                        confirmar_borrado(p["id"], titulo)
                st.caption(detalle_partida(p))

    # Estado del sistema: fuera de las pestañas para anclarlo al fondo de la barra (el CSS lo oculta en "Partida")
    with st.container(key="ajustes"):
        st.caption(
            f"RAG {'activo' if _vectorstore_available else 'sin inicializar'} · "
            f"agente {'ok' if _agent_available else 'con error'} · "
            f"partida `{st.session_state.partida_id[-4:]}`"
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


@st.cache_resource(show_spinner=False)
def precargar_rag() -> bool:
    """
    Importa y carga el RAG una sola vez al abrir la app. Importar langchain_community y
    FAISS tarda unos 3 s; sin esto se pagarían en la primera consulta del primer turno.
    (Cargar el índice en sí tarda menos de 0,3 s.)
    """
    try:
        from rag.retriever import get_retriever
        return get_retriever()._ensure_loaded()
    except Exception:
        return False


if _vectorstore_available:
    with st.spinner("Preparando el reglamento…"):
        precargar_rag()

# ---------------------------------------------------------------------------
# MENSAJE DE BIENVENIDA
# ---------------------------------------------------------------------------
BIENVENIDA = """**Bienvenido, aventurero.** Soy tu Dungeon Master.

Ante ti se alza la entrada de una antigua mazmorra élfica, olvidada por los siglos. Las antorchas parpadean con una luz tenue y el aire huele a piedra húmeda y a algo antiguo.

Antes de cruzar el umbral, forjemos a tu héroe. Dime, en un solo mensaje:

- **Raza:** humano, elfo, enano o mediano
- **Clase:** guerrero, mago, pícaro o clérigo
- **Nombre:** el que quieras

Si prefieres que lo elija yo, escribe *sortea* y te propondré una combinación.

*El destino aguarda.*"""

def resumen_para_retomar() -> str:
    """
    Texto con el que se orienta al modelo cuando el chat de la partida existe pero su MEMORIA no
    (por ejemplo, si se borró storage/memoria.db). Se siembra en el hilo; no se muestra al jugador.
    """
    try:
        estado = _sm.get_state()
        j = estado["jugador"]
        if not j.get("ficha_creada"):
            return BIENVENIDA
        sala = estado["sala_actual"]
        lugar = DUNGEON_LAYOUT.get(sala, {}).get("nombre", sala)
        texto = (
            f"Se retoma una partida guardada: {j['nombre']}, {j['raza']} {j['clase']} de nivel {j['nivel']}, "
            f"con {j['hp']}/{j['max_hp']} puntos de vida, en la sala {lugar}."
        )
        if estado["npcs"]:
            texto += " Enemigos en combate: " + ", ".join(d["nombre"] for d in estado["npcs"].values()) + "."
        return texto + " La memoria detallada de la conversación anterior no está disponible."
    except Exception:
        return BIENVENIDA


_pid = st.session_state.partida_id
if not st.session_state.messages:
    # Partida nueva: bienvenida fija (no gasta una llamada al modelo) registrada en la memoria del agente
    seed_conversation(_pid, BIENVENIDA)
    st.session_state.messages.append({"role": "assistant", "content": BIENVENIDA, "tool_calls": []})
    guardar_historial()
else:
    # Partida retomada. Si la última entrada es del jugador, el turno anterior se cortó (F5, Stop, caída)
    if st.session_state.messages[-1]["role"] == "user":
        st.session_state.messages.append({
            "role": "assistant", "tool_calls": [], "reasoning": "", "seg": None,
            "content": "*El turno anterior se interrumpió antes de terminar. Puedes repetir tu acción.*",
        })
        guardar_historial()
    # Si el chat existe pero la memoria del agente no, se la orienta con el estado guardado
    if get_conversation_length(_pid) == 0:
        seed_conversation(_pid, resumen_para_retomar())

# ---------------------------------------------------------------------------
# HISTORIAL DE MENSAJES
# ---------------------------------------------------------------------------
AVATAR = {"assistant": ":material/auto_stories:", "user": ":material/person:"}


def ancla_mensaje(idx: int):
    """Marca (invisible) el mensaje del jugador para que el riel lateral pueda saltar hasta él."""
    st.html(f'<span id="msg-{idx}" class="msg-ancla"></span>')


def html_riel(mensajes: list) -> str:
    """
    Riel lateral estilo ChatGPT: un punto por cada mensaje del jugador; al pasar el mouse muestra
    su mini título y al hacer clic salta a esa parte de la conversación.
    """
    items = []
    for i, m in enumerate(mensajes):
        if m["role"] != "user":
            continue
        titulo = " ".join(texto_de(m["content"]).split())
        titulo = titulo if len(titulo) <= 44 else titulo[:43].rstrip() + "…"
        items.append(f'<a href="#msg-{i}" aria-label="Ir al mensaje: {html.escape(titulo)}">'
                     f'<span class="t">{html.escape(titulo)}</span><i></i></a>')
    return f'<nav class="riel" aria-label="Mensajes de la conversación">{"".join(items)}</nav>' if items else ""


riel_box = st.empty()          # posición fija en pantalla: se redibuja al llegar un mensaje nuevo


def pintar_riel():
    contenido = html_riel(st.session_state.messages)
    if contenido:
        riel_box.html(contenido)
    else:
        riel_box.empty()          # st.html no acepta un cuerpo vacío

for idx, mensaje in enumerate(st.session_state.messages):
    rol = mensaje["role"]
    tool_calls = mensaje.get("tool_calls", [])

    with st.chat_message(rol, avatar=AVATAR.get(rol)):
        if rol == "assistant":
            # Orden fijo: razonamiento, herramientas (en orden de invocación), respuesta
            if mensaje.get("reasoning"):
                renderizar_razonamiento(mensaje["reasoning"], key=f"razon_{idx}", abierto=pasos_abiertos)
            for registro in tool_calls:
                paso_estatico(registro, abierto=pasos_abiertos)
            with st.container(key=f"narr_{idx}"):
                st.markdown(texto_de(mensaje["content"]))
            if uso_mapa(tool_calls):
                boton_descarga_mapa(f"dl_mapa_{idx}")
            if (pdf := pdf_generado(tool_calls)):
                boton_descarga_pdf(pdf, f"dl_pdf_{idx}")
            if mensaje.get("seg") is not None:
                st.caption(resumen_turno(len(tool_calls), mensaje["seg"]))
        else:
            ancla_mensaje(idx)
            st.markdown(texto_de(mensaje["content"]))

pintar_riel()

# ---------------------------------------------------------------------------
# INPUT DEL USUARIO + STREAMING DEL AGENTE
# ---------------------------------------------------------------------------
# submit_mode="disable": mientras el agente trabaja no se puede enviar otro mensaje
if prompt := st.chat_input("¿Qué hace tu personaje?", submit_mode="disable"):

    st.session_state.messages.append({"role": "user", "content": prompt, "tool_calls": []})
    guardar_historial()      # el mensaje del jugador se guarda ANTES del turno: si se corta, no se pierde
    pintar_riel()
    with st.chat_message("user", avatar=AVATAR["user"]):
        ancla_mensaje(len(st.session_state.messages) - 1)
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
        t_turno = time.time()

        for ev in stream_agent(prompt, thread_id=st.session_state.partida_id):
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
                registro = {"name": ev["name"], "input": ev["args"], "output": "", "_t": time.time()}
                tool_calls.append(registro)
                # El modelo puede razonar y hablar en varias llamadas dentro de un mismo turno:
                # se separa lo anterior a la tool de lo que venga después
                if texto and not texto.endswith("\n\n"):
                    texto += "\n\n"
                if razonamiento and not razonamiento.endswith("\n\n"):
                    razonamiento += "\n\n"
                # El paso aparece apenas el agente decide invocar la tool, ya con sus argumentos
                with tools_box:
                    estado = st.status(etiqueta_tool(registro), type="step", state="running",
                                       expanded=True)          # en vivo siempre abierto: se ve cada paso
                    with estado:
                        mostrar_args(registro["input"])
                pasos[ev["id"]] = (registro, estado)

            elif tipo == "tool_result":
                if ev["id"] in pasos:
                    registro, estado = pasos[ev["id"]]
                    registro["output"] = ev["output"]
                    registro["seg"] = round(time.time() - registro.pop("_t", time.time()), 1)
                    with estado:
                        mostrar_resultado(registro)
                    estado.update(label=etiqueta_tool(registro), state=estado_tool(registro))
                    with tools_box:
                        mostrar_dado(registro)

            elif tipo == "text":
                texto += ev["text"]
                texto_box.markdown(texto + "▌")

            elif tipo == "error":
                error = ev["message"]

        seg_turno = time.time() - t_turno
        for registro in tool_calls:
            registro.pop("_t", None)      # marca interna de tiempo; no se guarda en el historial

        if razon_estado is not None:
            razon_estado.update(
                label=":violet[:material/psychology:] Razonamiento", state="complete",
                expanded=pasos_abiertos,
            )
        for _, estado_paso in pasos.values():      # al terminar la respuesta, los pasos se cierran
            estado_paso.update(expanded=pasos_abiertos)
        texto_box.markdown(texto)
        if not error:
            st.caption(resumen_turno(len(tool_calls), seg_turno))

        # Exportar el mapa desde el chat si este turno lo actualizó (misma key que usará el historial)
        if uso_mapa(tool_calls):
            boton_descarga_mapa(f"dl_mapa_{len(st.session_state.messages)}")
        if (pdf := pdf_generado(tool_calls)):
            boton_descarga_pdf(pdf, f"dl_pdf_{len(st.session_state.messages)}")

        if error:
            st.error(
                f"{error}\n\nPuedes reenviar tu mensaje o reiniciar con «Nueva partida».",
                title="El Dungeon Master tropezó con un hechizo roto",
                icon=":material/error:",
            )

        # Guardar en el historial (con razonamiento, tools y tiempo para poder re-renderizar)
        st.session_state.messages.append({
            "role": "assistant",
            "content": texto or (f"*[Error del sistema: {error}]*" if error else ""),
            "tool_calls": tool_calls,
            "reasoning": razonamiento,
            "seg": None if error else seg_turno,
        })
        guardar_historial()      # también si hubo error: el chat queda tal como el jugador lo vio

    # Refrescar para actualizar el sidebar (HP, mapa, etc.)
    st.rerun()
