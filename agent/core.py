"""
core.py - Orquestador del Agente Dungeon Master
Gestiona la creación, inicialización y ejecución del agente LLM con tools de LangChain.
Implementa el loop de tool calling para que el agente pueda ejecutar múltiples herramientas
antes de dar la respuesta final al usuario.
"""

import os
import sys
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

# Cargar variables de entorno desde .env
load_dotenv()

# Asegurar que el directorio raíz del proyecto esté en el path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importar las herramientas del agente DM
from agent.tools import query_rules_rag, execute_dice_roll, switch_and_render_map

# Importar el prompt del sistema
from agent.prompt import DM_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# Estado global del agente (patrón Singleton)
# ---------------------------------------------------------------------------

# Instancia global del agente (se crea una sola vez)
_agent = None

# Historial de mensajes por hilo de conversación (thread_id → lista de mensajes)
_conversation_history: dict[str, list] = {}

# Lista de todas las tools disponibles para el agente
_TOOLS = [query_rules_rag, execute_dice_roll, switch_and_render_map]

# Mapa de nombre de tool → función callable (para ejecutar las tools)
_TOOL_MAP = {tool.name: tool for tool in _TOOLS}

# Número máximo de iteraciones del loop de tool calling (evitar loops infinitos)
_MAX_TOOL_ITERATIONS = 10


# ---------------------------------------------------------------------------
# Creación y gestión del agente
# ---------------------------------------------------------------------------

def create_agent() -> dict:
    """
    Crea y configura el agente DM con el modelo LLM y las herramientas.

    Returns:
        dict con claves:
            - "llm_with_tools": El LLM con las tools vinculadas (ChatGoogleGenerativeAI)
            - "memory": Objeto de gestión de memoria (diccionario de historiales por thread)

    Raises:
        ValueError: Si GEMINI_API_KEY no está configurada en el entorno
    """
    # Verificar que la API key esté disponible
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "GEMINI_API_KEY no está configurada. "
            "Crea o edita el archivo .env con: GEMINI_API_KEY=tu_clave_aqui"
        )

    # Crear el modelo LLM de Google Gemini
    # temperature=0.8 para respuestas creativas y narrativas
    llm = ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite",
        temperature=0.8,
        google_api_key=api_key,
        convert_system_message_to_human=False,
    )

    # Vincular las herramientas al LLM para que pueda invocarlas
    llm_with_tools = llm.bind_tools(_TOOLS)

    # La "memoria" en este caso es el diccionario global de historiales
    # (compatible con la interfaz esperada)
    memory = _conversation_history

    return {
        "llm_with_tools": llm_with_tools,
        "memory": memory,
    }


def get_agent() -> dict:
    """
    Retorna la instancia singleton del agente, creándola si no existe.

    Returns:
        dict con el agente configurado (ver create_agent())
    """
    global _agent

    if _agent is None:
        _agent = create_agent()

    return _agent


# ---------------------------------------------------------------------------
# Ejecución de una tool individual
# ---------------------------------------------------------------------------

def _execute_tool(tool_name: str, tool_input: dict) -> str:
    """
    Ejecuta una tool específica por nombre con los argumentos dados.

    Args:
        tool_name: Nombre de la tool a ejecutar (debe estar en _TOOL_MAP)
        tool_input: Diccionario con los argumentos de la tool

    Returns:
        String con el resultado de la tool, o mensaje de error si falla
    """
    if tool_name not in _TOOL_MAP:
        return f"❌ Tool desconocida: '{tool_name}'. Tools disponibles: {list(_TOOL_MAP.keys())}"

    tool_func = _TOOL_MAP[tool_name]

    try:
        # Invocar la tool con los argumentos proporcionados
        result = tool_func.invoke(tool_input)
        return str(result)
    except Exception as e:
        return f"❌ Error al ejecutar la tool '{tool_name}': {e}"


# ---------------------------------------------------------------------------
# Función principal de ejecución del agente
# ---------------------------------------------------------------------------

def run_agent(user_message: str, thread_id: str = "default") -> dict:
    """
    Ejecuta el agente con el mensaje del usuario y retorna la respuesta final.

    Implementa el loop de tool calling:
    1. Envía el mensaje al LLM junto con el historial de conversación
    2. Si el LLM responde con tool_calls, ejecuta cada herramienta
    3. Agrega los resultados de las tools al historial
    4. Repite hasta que el LLM dé una respuesta de texto final (sin tool_calls)
    5. Retorna la respuesta final junto con el registro de tools usadas

    Args:
        user_message: El mensaje del usuario (lo que hace/dice el jugador)
        thread_id: Identificador único del hilo de conversación (para mantener historial)
                   Usar un ID único por sesión de juego.

    Returns:
        dict con claves:
            - "response": str con la respuesta narrativa final del DM
            - "tool_calls": list de dicts con las tools ejecutadas:
                [{"name": str, "input": dict, "output": str}, ...]

    Raises:
        No lanza excepciones; los errores se capturan y se retornan en "response"
    """
    try:
        # Obtener el agente singleton
        agent = get_agent()
        llm_with_tools = agent["llm_with_tools"]

        # Inicializar el historial para este thread si no existe
        if thread_id not in _conversation_history:
            # Comenzar el historial con el prompt del sistema
            _conversation_history[thread_id] = [
                SystemMessage(content=DM_SYSTEM_PROMPT)
            ]

        # Agregar el mensaje del usuario al historial
        _conversation_history[thread_id].append(
            HumanMessage(content=user_message)
        )

        # Registro de todas las tools ejecutadas en esta llamada
        executed_tool_calls = []

        # --- Loop de tool calling ---
        for iteration in range(_MAX_TOOL_ITERATIONS):
            # Enviar el historial completo al LLM
            ai_response = llm_with_tools.invoke(_conversation_history[thread_id])

            # Agregar la respuesta del LLM al historial
            _conversation_history[thread_id].append(ai_response)

            # Verificar si el LLM quiere ejecutar tools
            if not ai_response.tool_calls:
                # No hay más tool calls → el LLM dio la respuesta final de texto
                final_response = ai_response.content
                break

            # Procesar cada tool call solicitada por el LLM
            for tool_call in ai_response.tool_calls:
                tool_name = tool_call["name"]
                tool_input = tool_call["args"]
                tool_call_id = tool_call["id"]

                # Ejecutar la tool
                tool_output = _execute_tool(tool_name, tool_input)

                # Registrar la tool ejecutada para el retorno
                executed_tool_calls.append({
                    "name": tool_name,
                    "input": tool_input,
                    "output": tool_output,
                })

                # Agregar el resultado de la tool al historial como ToolMessage
                _conversation_history[thread_id].append(
                    ToolMessage(
                        content=tool_output,
                        tool_call_id=tool_call_id,
                    )
                )

        else:
            # Se alcanzó el límite de iteraciones sin respuesta final
            final_response = (
                "⚠️ El Dungeon Master necesita un momento para organizar sus pensamientos... "
                "(Se alcanzó el límite de procesamiento interno. Intenta reformular tu acción.)"
            )

        return {
            "response": final_response,
            "tool_calls": executed_tool_calls,
        }

    except Exception as e:
        # Capturar cualquier error inesperado y retornarlo de forma amigable
        error_message = (
            f"❌ Error en el Dungeon Master AI: {e}\n\n"
            "El Dungeon Master tuvo un contratiempo técnico. "
            "Por favor, intenta de nuevo o reinicia la sesión."
        )
        return {
            "response": error_message,
            "tool_calls": [],
        }


# ---------------------------------------------------------------------------
# Funciones auxiliares de gestión del historial
# ---------------------------------------------------------------------------

def clear_conversation(thread_id: str = "default") -> None:
    """
    Limpia el historial de conversación de un thread específico.
    Útil al iniciar una nueva partida o reiniciar la sesión.

    Args:
        thread_id: ID del hilo de conversación a limpiar
    """
    if thread_id in _conversation_history:
        del _conversation_history[thread_id]

    # Reiniciar el thread con solo el prompt del sistema
    _conversation_history[thread_id] = [
        SystemMessage(content=DM_SYSTEM_PROMPT)
    ]


def get_conversation_length(thread_id: str = "default") -> int:
    """
    Retorna la cantidad de mensajes en el historial de un thread.

    Args:
        thread_id: ID del hilo de conversación

    Returns:
        int con la cantidad de mensajes (incluyendo el SystemMessage inicial)
    """
    if thread_id not in _conversation_history:
        return 0
    return len(_conversation_history[thread_id])


def reset_agent() -> None:
    """
    Reinicia completamente el agente, limpiando el singleton y todos los historiales.
    Útil para testing o cuando se cambia la configuración de la API key.
    """
    global _agent
    _agent = None
    _conversation_history.clear()
