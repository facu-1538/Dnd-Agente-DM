"""
tools.py - Herramientas del agente Dungeon Master
Implementa las tools decoradas con @tool de LangChain que el agente puede invocar.
"""

from langchain_core.tools import tool
import random
import re
import json
import sys
import os

# Asegurar que el directorio raíz del proyecto esté en el path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# TOOL 1: Consulta a la base de conocimientos RAG de D&D 5e
# ---------------------------------------------------------------------------

@tool
def query_rules_rag(query: str, category: str = "general") -> str:
    """
    Consulta la base de conocimientos de D&D 5e usando recuperación semántica (RAG).

    Usa esta herramienta cuando el jugador pregunta sobre:
    - Hechizos: nombre, efecto, componentes, duración, nivel, escuela
    - Monstruos: estadísticas, CR, HP, AC, acciones, resistencias
    - Reglas del juego: combate, movimiento, condiciones, acciones especiales
    - Condiciones: aturdido, paralizado, envenenado, asustado, etc.
    - Equipamiento: armas, armaduras, objetos mágicos

    Args:
        query: La consulta en lenguaje natural sobre la regla o elemento de D&D
        category: Categoría opcional (spells, monsters, conditions, equipment, general)

    Returns:
        Texto con la información recuperada de la base de conocimientos
    """
    # Importar aquí para evitar imports circulares al cargar el módulo
    try:
        from rag.retriever import get_retriever
    except ImportError as e:
        return f"⚠️ No se pudo importar el módulo RAG: {e}. Verifica que el módulo rag/ esté disponible."

    retriever = get_retriever()

    # Verificar que la base de conocimientos esté cargada
    if not retriever._ensure_loaded():
        return (
            "⚠️ La base de conocimientos no está disponible. "
            "Ejecuta 'python setup_rag.py' primero para ingesta y construcción del vector store."
        )

    # Buscar por categoría si se especifica, o búsqueda general
    if category and category != "general":
        return retriever.search_by_category(query, category, k=4)
    else:
        return retriever.search(query, k=4)


# ---------------------------------------------------------------------------
# TOOL 2: Ejecutor de tiradas de dados
# ---------------------------------------------------------------------------

def _parse_and_roll(dice_formula: str) -> dict:
    """
    Función interna que parsea y ejecuta una fórmula de dados.

    Fórmulas soportadas:
    - "1d20"        → 1 dado de 20 caras, sin modificador
    - "2d6+3"       → 2 dados de 6 caras, +3
    - "1d8-1"       → 1 dado de 8 caras, -1
    - "4d6kh3"      → 4 dados de 6 caras, mantiene los 3 más altos (keep highest)

    Returns:
        dict con claves: rolls, kept_rolls, modifier, total
    """
    formula = dice_formula.strip().lower().replace(" ", "")

    # Patrón para "keep highest": NdMkhH
    kh_match = re.match(r'^(\d+)d(\d+)kh(\d+)$', formula)
    # Patrón estándar con modificador opcional: NdM[+/-K]
    std_match = re.match(r'^(\d+)d(\d+)([+-]\d+)?$', formula)

    if kh_match:
        # Fórmula de "keep highest" (ej: 4d6kh3)
        num_dice = int(kh_match.group(1))
        die_sides = int(kh_match.group(2))
        keep_count = int(kh_match.group(3))

        # Validaciones básicas
        if die_sides < 2:
            raise ValueError(f"El dado debe tener al menos 2 caras, se recibió d{die_sides}")
        if keep_count > num_dice:
            raise ValueError(f"No se pueden mantener {keep_count} dados si solo se lanzan {num_dice}")

        all_rolls = [random.randint(1, die_sides) for _ in range(num_dice)]
        kept_rolls = sorted(all_rolls, reverse=True)[:keep_count]

        return {
            "rolls": all_rolls,
            "kept_rolls": kept_rolls,
            "modifier": 0,
            "total": sum(kept_rolls),
        }

    elif std_match:
        # Fórmula estándar (ej: 1d20+5, 2d6, 1d8-1)
        num_dice = int(std_match.group(1))
        die_sides = int(std_match.group(2))
        modifier_str = std_match.group(3)
        modifier = int(modifier_str) if modifier_str else 0

        if die_sides < 2:
            raise ValueError(f"El dado debe tener al menos 2 caras, se recibió d{die_sides}")
        if num_dice < 1:
            raise ValueError(f"Debe lanzarse al menos 1 dado, se recibió {num_dice}")

        rolls = [random.randint(1, die_sides) for _ in range(num_dice)]
        total = sum(rolls) + modifier

        return {
            "rolls": rolls,
            "kept_rolls": rolls,
            "modifier": modifier,
            "total": total,
        }

    else:
        raise ValueError(
            f"Fórmula de dados no reconocida: '{dice_formula}'. "
            "Formatos válidos: '1d20', '2d6+3', '1d8-1', '4d6kh3'"
        )


@tool
def execute_dice_roll(dice_formula: str, reason: str, target_dc_or_ac: int = 0) -> str:
    """
    Ejecuta una tirada de dados con la fórmula especificada y retorna el resultado detallado.

    DEBES usar esta herramienta para CUALQUIER acción que dependa del azar:
    - Ataques: "1d20+5" (d20 + modificador de ataque)
    - Daño: "1d8+3", "2d6" (dados de daño + modificador)
    - Salvaciones: "1d20+2" (d20 + modificador de salvación)
    - Habilidades: "1d20+4" (d20 + modificador de habilidad)
    - Iniciativa: "1d20+2" (d20 + modificador de Destreza)
    - Creación de personaje: "4d6kh3" (4d6 keep highest 3)

    Args:
        dice_formula: Fórmula de dados (ej: "1d20+5", "2d6", "4d6kh3")
        reason: Descripción de por qué se hace la tirada (ej: "Ataque con espada larga")
        target_dc_or_ac: CA del objetivo o CD de la salvación (0 = no comparar)

    Returns:
        JSON string con el resultado completo de la tirada
    """
    try:
        result = _parse_and_roll(dice_formula)

        # Determinar si hubo éxito comparando con el objetivo
        success = None
        success_text = ""
        if target_dc_or_ac > 0:
            success = result["total"] >= target_dc_or_ac
            success_text = "¡ÉXITO!" if success else "FALLO"

        # Construir texto descriptivo de la tirada
        rolls_display = str(result["rolls"])
        if result["kept_rolls"] != result["rolls"]:
            # Es una tirada kh: mostrar todos y luego los mantenidos
            rolls_display = f"{result['rolls']} → mantenidos: {result['kept_rolls']}"

        modifier_display = ""
        if result["modifier"] != 0:
            sign = "+" if result["modifier"] > 0 else ""
            modifier_display = f" {sign}{result['modifier']}"

        # Construir el objeto de resultado completo
        output = {
            "formula": dice_formula,
            "reason": reason,
            "rolls": result["rolls"],
            "kept_rolls": result["kept_rolls"],
            "modifier": result["modifier"],
            "total": result["total"],
            "target": target_dc_or_ac if target_dc_or_ac > 0 else None,
            "success": success,
            "success_text": success_text,
            "display": (
                f"🎲 {reason}: [{dice_formula}] "
                f"Dados: {rolls_display}{modifier_display} = **{result['total']}**"
                + (f" vs {target_dc_or_ac} → {success_text}" if target_dc_or_ac > 0 else "")
            ),
        }

        return json.dumps(output, ensure_ascii=False)

    except ValueError as e:
        # Error en la fórmula
        error_output = {
            "formula": dice_formula,
            "reason": reason,
            "error": str(e),
            "display": f"❌ Error en la tirada '{dice_formula}': {e}",
        }
        return json.dumps(error_output, ensure_ascii=False)

    except Exception as e:
        # Error inesperado
        error_output = {
            "formula": dice_formula,
            "reason": reason,
            "error": f"Error inesperado: {e}",
            "display": f"❌ Error inesperado al tirar '{dice_formula}': {e}",
        }
        return json.dumps(error_output, ensure_ascii=False)


# ---------------------------------------------------------------------------
# TOOL 3: Cambio y renderizado del mapa del dungeon
# ---------------------------------------------------------------------------

@tool
def switch_and_render_map(revealed_nodes: list) -> str:
    """
    Actualiza el mapa del dungeon revelando nuevas salas y renderiza la vista actual.

    Usa esta herramienta cuando:
    - El jugador se mueve a una nueva sala o área
    - El jugador explora una zona previamente desconocida
    - Un evento narrativo revela partes del mapa

    Args:
        revealed_nodes: Lista de IDs de salas/nodos a revelar en el mapa.
                       Ejemplo: ["sala_entrada", "corredor_norte", "sala_jefe"]

    Returns:
        String con el resultado de la actualización del mapa
    """
    try:
        from game_engine.state_manager import get_state_manager
        from game_engine.map_renderer import get_map_renderer
    except ImportError as e:
        return (
            f"⚠️ No se pudo cargar el motor de juego: {e}. "
            "Verifica que el módulo game_engine/ esté disponible."
        )

    try:
        # Obtener instancias singleton del state manager y el renderer
        state_manager = get_state_manager()
        map_renderer = get_map_renderer()

        # Validar que se proporcionaron nodos
        if not revealed_nodes:
            return "⚠️ No se proporcionaron nodos para revelar en el mapa."

        # Actualizar las salas reveladas en el estado del juego
        newly_revealed = []
        already_revealed = []

        for node_id in revealed_nodes:
            if isinstance(node_id, str) and node_id.strip():
                nodo_limpio = node_id.strip()
                salas_previas = state_manager.get_discovered_rooms()
                state_manager.discover_room(nodo_limpio)
                # Si la sala no estaba antes, es nueva
                if nodo_limpio not in salas_previas:
                    newly_revealed.append(nodo_limpio)
                else:
                    already_revealed.append(nodo_limpio)

        # Renderizar el mapa con niebla de guerra actualizada
        mapa_path = map_renderer.render_fog_of_war(
            discovered_rooms=state_manager.get_discovered_rooms()
        )
        render_success = bool(mapa_path)

        # Construir mensaje de resultado
        result_parts = []

        if newly_revealed:
            result_parts.append(f"🗺️ **Nuevas áreas reveladas**: {', '.join(newly_revealed)}")
        if already_revealed:
            result_parts.append(f"ℹ️ Ya exploradas: {', '.join(already_revealed)}")

        if render_success:
            result_parts.append("✅ Mapa actualizado y renderizado correctamente.")
            result_parts.append("El mapa ahora muestra las nuevas áreas en el panel lateral.")
        else:
            result_parts.append(
                "⚠️ El mapa fue actualizado en el estado del juego pero no se pudo renderizar la imagen. "
                "Verifica que el renderer esté configurado correctamente."
            )

        return "\n".join(result_parts)

    except Exception as e:
        return (
            f"❌ Error al actualizar el mapa: {e}. "
            "El juego puede continuar normalmente; el mapa se actualizará en el próximo movimiento."
        )
