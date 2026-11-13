"""
core.py - Orquestador del Agente Dungeon Master

Construye el agente con `create_agent` de LangChain (sin Deep Agents) y expone:
  - stream_agent(): generador de eventos para que la UI muestre en vivo cada tool,
    el razonamiento y el texto de la respuesta.
  - run_agent():    versión bloqueante (la usa la bienvenida de app.py).

Memoria de corto plazo: un checkpointer (InMemorySaver) guarda el estado de la
conversación por `thread_id`. El agente y el checkpointer viven a nivel de módulo;
Streamlit re-ejecuta app.py en cada interacción pero NO re-importa este módulo,
por eso la memoria sobrevive entre turnos. Se pierde al reiniciar el proceso.
"""

import os
import re
import sys
from typing import Iterator

from dotenv import load_dotenv
from langchain.agents import create_agent as lc_create_agent
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

# Cargar variables de entorno desde .env
load_dotenv()

# Asegurar que el directorio raíz del proyecto esté en el path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.tools import (
    query_rules_rag,
    execute_dice_roll,
    switch_and_render_map,
    damage_player,
    heal_player,
    set_character,
    manage_enemy,
)
from agent.prompt import DM_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

# Modelo configurable por entorno (por defecto el que venía usando el equipo)
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")

# Pedir los bloques de razonamiento al proveedor. Si el modelo no los devuelve,
# la UI simplemente no muestra nada (ver README). Se puede apagar con DM_INCLUDE_THOUGHTS=0.
INCLUDE_THOUGHTS = os.getenv("DM_INCLUDE_THOUGHTS", "1") != "0"

# Tokens máximos que el modelo puede dedicar a pensar (-1 = dinámico, sin tope). Medido:
# el mismo turno de combate tardó 82 s con -1 y 7 s sin razonamiento, así que el pensamiento
# dinámico es un riesgo de latencia. Configurable por entorno.
THINKING_BUDGET = int(os.getenv("GEMINI_THINKING_BUDGET", "-1"))

# Tope de pasos del grafo (cada tool call consume ~2). Evita bucles infinitos.
RECURSION_LIMIT = 25

# Intentos TOTALES por llamada al modelo (incluye el pedido original: 4 = 1 pedido + 3
# reintentos, con esperas de ~1, 2 y 4 s). Se reintenta ante 429/5xx y también ante
# timeouts. Con 6 (el valor por defecto del cliente) la pantalla podía quedar quieta
# mucho más tiempo. Configurable por entorno.
MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", "4"))

# Tope en segundos de CADA intento HTTP (antes: sin tope, una respuesta colgada esperaba
# indefinidamente). Peor caso por llamada al modelo: MAX_RETRIES x TIMEOUT + esperas
# (~127 s con los valores por defecto). Acota una respuesta colgada; no una saturación
# sostenida de la cuota. Nota: step_timeout de LangGraph NO sirve para esto (se probó):
# lanza el error pero espera igual a que termine el hilo bloqueado.
TIMEOUT = float(os.getenv("GEMINI_TIMEOUT", "30"))

_TOOLS = [
    query_rules_rag, execute_dice_roll, switch_and_render_map,
    damage_player, heal_player, set_character, manage_enemy,
]

# Checkpointer y agente: singletons de módulo (ver docstring)
_checkpointer = InMemorySaver()
_agent = None


# ---------------------------------------------------------------------------
# Creación del agente
# ---------------------------------------------------------------------------

def build_agent():
    """
    Crea el agente con `create_agent`.

    Reintentos: `max_retries` se traduce a HttpRetryOptions del SDK de Google, que
    reintenta ante 408/429/5xx con backoff exponencial (1 s hasta 60 s, con jitter).
    Por eso no se usa middleware de reintentos. Límite: un 429 por cuota DIARIA
    agotada no se recupera reintentando; en ese caso el agente devuelve un error
    legible (ver _mensaje_error).

    Raises:
        ValueError: si GEMINI_API_KEY no está configurada.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY no está configurada. "
            "Crea o edita el archivo .env con: GEMINI_API_KEY=tu_clave_aqui"
        )

    llm = ChatGoogleGenerativeAI(
        model=MODEL_NAME,
        temperature=0.8,
        google_api_key=api_key,
        max_retries=MAX_RETRIES,
        timeout=TIMEOUT,
        include_thoughts=INCLUDE_THOUGHTS,
        # Verificado con gemini-3.1-flash-lite: include_thoughts SOLO no devuelve nada;
        # hace falta un thinking_budget para recibir el bloque "thinking". Gemini entrega
        # un resumen del razonamiento (en inglés). -1 = dinámico (sin tope).
        thinking_budget=THINKING_BUDGET if INCLUDE_THOUGHTS else None,
    )

    return lc_create_agent(
        model=llm,
        tools=_TOOLS,
        system_prompt=DM_SYSTEM_PROMPT,
        checkpointer=_checkpointer,
    )


def get_agent():
    """Retorna el agente singleton, creándolo la primera vez."""
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _estado_jugador() -> str:
    """
    Línea con el estado real de la partida (personaje, vida, ubicación, enemigos vivos).

    Se antepone al mensaje del jugador en CADA turno: así el modelo nunca trabaja con
    datos viejos ni inventados, y no hace falta una tool ni una ronda extra para consultarlos.
    """
    try:
        from game_engine.state_manager import get_state_manager
        estado = get_state_manager().get_state()
        j = estado["jugador"]

        if j.get("ficha_creada"):
            ficha = f"{j['nombre']}, {j['raza']} {j['clase']} (nivel {j['nivel']})"
        else:
            ficha = "personaje SIN crear todavía"

        try:
            from game_engine.map_renderer import DUNGEON_LAYOUT
            sala = estado["sala_actual"]
            ubicacion = f"{DUNGEON_LAYOUT[sala]['nombre']} ({sala})"
        except Exception:
            ubicacion = estado.get("sala_actual", "?")

        enemigos = ", ".join(
            f"{d['nombre']} {d['hp']}/{d['max_hp']} HP" for d in estado["npcs"].values()
        ) or "ninguno"

        return (
            f"[ESTADO: {ficha} | HP {j['hp']}/{j['max_hp']} | oro {j['oro']}, XP {j['experiencia']} | "
            f"ubicación: {ubicacion} | enemigos: {enemigos}]\n"
        )
    except Exception:
        return ""


def _mensaje_error(e: Exception) -> str:
    """Traduce un error del proveedor a un mensaje que el jugador pueda entender y accionar."""
    texto = str(e)
    if re.search(r"\b429\b|RESOURCE_EXHAUSTED|quota", texto, flags=re.I):
        return ("Gemini alcanzó su límite de uso (cuota gratuita). Esperá cerca de un minuto y "
                "volvé a enviar tu mensaje; si se agotó la cuota diaria, probá más tarde.")
    if re.search(r"API key|API_KEY|PERMISSION_DENIED|UNAUTHENTICATED", texto, flags=re.I):
        return "La clave de la API de Gemini no es válida o no tiene permisos. Revisá GEMINI_API_KEY en el archivo .env."
    if re.search(r"NOT_FOUND|\b404\b", texto):
        return "El modelo configurado no existe para tu cuenta. Probá con otro en GEMINI_MODEL."
    return f"Error en el Dungeon Master AI: {texto[:300]}"


def seed_conversation(thread_id: str, bienvenida: str) -> bool:
    """
    Registra en la memoria del hilo la bienvenida que la UI muestra SIN llamar al modelo,
    para que el agente sepa qué le dijo al jugador. Se guarda como un turno (inicio del
    jugador + respuesta del DM) para que la conversación siga alternando usuario/modelo.

    Devuelve False si no se pudo; en ese caso la partida funciona igual, solo que el
    agente no recuerda la bienvenida.
    """
    try:
        get_agent().update_state(
            {"configurable": {"thread_id": thread_id}},
            {"messages": [
                HumanMessage(content="INICIO_DE_PARTIDA: el jugador abre la aplicación."),
                AIMessage(content=bienvenida),
            ]},
            # Sin as_node el grafo queda con el nodo "model" pendiente de ejecutarse.
            # Declarando que la actualización la hizo "model" el hilo queda en reposo.
            as_node="model",
        )
        return True
    except Exception:
        return False


def _split_blocks(content) -> tuple[str, str]:
    """
    Separa el `content` crudo de un chunk en (razonamiento, texto).

    Se lee el contenido crudo y no `chunk.content_blocks`: esa vista estandarizada
    puede descartar silenciosamente bloques de razonamiento según el proveedor.
    `content` es un str (solo texto) o una lista de bloques: str, {"type": "text"}
    o {"type": "thinking"|"reasoning"} (la clave del texto varía con el proveedor).
    """
    if isinstance(content, str):
        return "", content
    reasoning, text = [], []
    for block in content or []:
        if isinstance(block, str):
            text.append(block)
        elif isinstance(block, dict):
            kind = block.get("type")
            if kind in ("reasoning", "thinking"):
                reasoning.append(block.get("reasoning") or block.get("thinking") or "")
            elif kind == "text":
                text.append(block.get("text", "") or "")
    return "".join(reasoning), "".join(text)


# ---------------------------------------------------------------------------
# Ejecución con streaming
# ---------------------------------------------------------------------------

def stream_agent(user_message: str, thread_id: str = "default") -> Iterator[dict]:
    """
    Ejecuta el agente y emite eventos a medida que ocurren.

    Eventos (dict con clave "type"):
      - "reasoning":   {"text"}                   fragmento de razonamiento del modelo
      - "text":        {"text"}                   fragmento de la respuesta
      - "tool_call":   {"id", "name", "args"}     el agente decidió invocar una tool
      - "tool_result": {"id", "name", "output"}   resultado de esa tool
      - "error":       {"message"}                fallo; el flujo termina sin lanzar excepción

    Args:
        user_message: lo que hace/dice el jugador
        thread_id: hilo de conversación (el checkpointer guarda la memoria por hilo)
    """
    try:
        agent = get_agent()
        config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": RECURSION_LIMIT,
        }
        # El HP real se inyecta en CADA turno para que el modelo nunca vea un valor viejo
        entrada = {"messages": [{"role": "user", "content": _estado_jugador() + user_message}]}

        for mode, payload in agent.stream(
            entrada, config=config, stream_mode=["updates", "messages"]
        ):
            if mode == "messages":
                chunk, _meta = payload
                # En este modo también llegan ToolMessages: solo nos interesan los chunks del LLM
                if not isinstance(chunk, AIMessageChunk):
                    continue
                reasoning, text = _split_blocks(chunk.content)
                if reasoning:
                    yield {"type": "reasoning", "text": reasoning}
                if text:
                    yield {"type": "text", "text": text}

            elif mode == "updates":
                # Aquí los mensajes llegan completos (tool calls con argumentos enteros)
                for _node, update in payload.items():
                    for msg in (update or {}).get("messages", []):
                        if isinstance(msg, AIMessage):
                            for tc in msg.tool_calls:
                                yield {
                                    "type": "tool_call",
                                    "id": tc["id"],
                                    "name": tc["name"],
                                    "args": tc["args"],
                                }
                        elif isinstance(msg, ToolMessage):
                            yield {
                                "type": "tool_result",
                                "id": msg.tool_call_id,
                                "name": msg.name,
                                "output": str(msg.content),
                            }

    except GraphRecursionError:
        yield {
            "type": "error",
            "message": "El Dungeon Master se enredó en demasiados pasos internos. "
                       "Intenta reformular tu acción.",
        }
    except Exception as e:
        yield {"type": "error", "message": _mensaje_error(e)}


def run_agent(user_message: str, thread_id: str = "default") -> dict:
    """
    Versión bloqueante: consume stream_agent() y devuelve el resultado completo.

    Returns:
        {"response": str, "tool_calls": [{"name", "input", "output"}, ...]}
        Nunca lanza excepciones; los errores se devuelven en "response".
    """
    texto, tool_calls, pendientes = [], [], {}
    for ev in stream_agent(user_message, thread_id):
        if ev["type"] == "text":
            texto.append(ev["text"])
        elif ev["type"] == "tool_call":
            tc = {"name": ev["name"], "input": ev["args"], "output": ""}
            pendientes[ev["id"]] = tc
            tool_calls.append(tc)
        elif ev["type"] == "tool_result" and ev["id"] in pendientes:
            pendientes[ev["id"]]["output"] = ev["output"]
        elif ev["type"] == "error":
            return {"response": f"❌ {ev['message']}", "tool_calls": tool_calls}
    return {"response": "".join(texto), "tool_calls": tool_calls}


# ---------------------------------------------------------------------------
# Gestión de hilos
# ---------------------------------------------------------------------------

def clear_conversation(thread_id: str = "default") -> None:
    """Borra la memoria de un hilo (nueva partida)."""
    _checkpointer.delete_thread(thread_id)


def get_conversation_length(thread_id: str = "default") -> int:
    """Cantidad de mensajes guardados por el checkpointer para un hilo."""
    try:
        estado = get_agent().get_state({"configurable": {"thread_id": thread_id}})
        return len(estado.values.get("messages", []))
    except Exception:
        return 0


def reset_agent() -> None:
    """Descarta el agente singleton (p. ej. si cambió la API key)."""
    global _agent
    _agent = None
