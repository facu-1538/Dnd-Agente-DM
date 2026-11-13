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
from typing import Literal

# Asegurar que el directorio raíz del proyecto esté en el path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _log(tipo: str, texto: str) -> None:
    """Registra un evento en la partida. Nunca rompe la tool si el registro falla."""
    try:
        from game_engine.state_manager import get_state_manager
        get_state_manager().log_event(tipo, texto)
    except Exception:
        pass


def _json(datos: dict) -> str:
    return json.dumps(datos, ensure_ascii=False)


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
        category: Documento donde buscar. Valores válidos: spells, monsters, conditions,
                  armor, weapons, rules o general (busca en todos). Con cualquier otro
                  valor se busca en todos los documentos.

    Returns:
        Fragmentos recuperados, cada uno con su documento de origen ("archivo.txt › Título"),
        y al final la línea FUENTES CONSULTADAS. Al usar esta información en la respuesta,
        cita la fuente así: (Fuente: monsters.txt › Goblin).
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
        resultado = retriever.search_by_category(query, category, k=4)
    else:
        resultado = retriever.search(query, k=4)

    # Registro de la partida: qué se consultó y de qué documentos salió
    fuentes = re.search(r"^FUENTES CONSULTADAS: (.+)$", resultado, flags=re.M)
    _log("reglas", f"Consulta al reglamento: «{query}»" + (f" → {fuentes.group(1)}" if fuentes else ""))
    return resultado


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

        veredicto = "" if success is None else (" (éxito)" if success else " (fallo)")
        _log("dados", f"{reason}: {dice_formula} → {result['total']}{veredicto}")
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
# TOOL 3: Movimiento por el dungeon y mapa
# ---------------------------------------------------------------------------

@tool
def switch_and_render_map(revealed_nodes: list[str], current_room: str = "") -> str:
    """
    Mueve al jugador por el dungeon: revela salas, fija su ubicación y redibuja el mapa.
    Úsala cuando el jugador entra a una sala o descubre una zona nueva.

    Salas válidas (cualquier otro ID se rechaza): sala_entrada, sala_biblioteca, sala_armeria,
    sala_guardia, sala_archivo, sala_tesoro, sala_mazmorra, sala_jefe.
    Conexiones: entrada -> biblioteca / armeria / guardia; biblioteca -> archivo;
    armeria -> tesoro -> jefe; guardia -> mazmorra.

    Args:
        revealed_nodes: IDs de las salas a revelar. Ejemplo: ["sala_armeria"]
        current_room: ID de la sala donde queda el jugador. Si se omite, es la última
                      sala de revealed_nodes.

    Returns:
        Salas nuevas, ubicación actual y estado del mapa.
    """
    try:
        from game_engine.state_manager import get_state_manager
        from game_engine.map_renderer import get_map_renderer, DUNGEON_LAYOUT
    except ImportError as e:
        return f"⚠️ No se pudo cargar el motor de juego: {e}."

    try:
        sm = get_state_manager()
        validos, invalidos = [], []
        for nodo in revealed_nodes or []:
            nodo = nodo.strip() if isinstance(nodo, str) else ""
            if nodo in DUNGEON_LAYOUT:
                validos.append(nodo)
            elif nodo:
                invalidos.append(nodo)

        # Sala donde queda el jugador: la indicada, o la última revelada
        destino = (current_room or "").strip()
        if destino and destino not in DUNGEON_LAYOUT:
            invalidos.append(destino)
            destino = ""
        destino = destino or (validos[-1] if validos else "")

        if not destino:
            return f"⚠️ No se indicó ninguna sala válida. IDs válidos: {', '.join(DUNGEON_LAYOUT)}."

        previas = sm.get_discovered_rooms()
        nuevas = [n for n in validos if n not in previas]
        ya_vistas = [n for n in validos if n in previas]
        for nodo in validos:
            sm.discover_room(nodo)
        sm.move_to_room(destino, DUNGEON_LAYOUT[destino]["nombre"])

        mapa_ok = bool(get_map_renderer().render_fog_of_war(discovered_rooms=sm.get_discovered_rooms()))

        lineas = []
        if nuevas:
            lineas.append(f"Salas nuevas: {', '.join(nuevas)}")
        if ya_vistas:
            lineas.append(f"Ya exploradas: {', '.join(ya_vistas)}")
        lineas.append(f"Ubicación actual: {DUNGEON_LAYOUT[destino]['nombre']} ({destino})")
        if invalidos:
            lineas.append(f"⚠️ IDs inexistentes (ignorados): {', '.join(invalidos)}. "
                          f"Válidos: {', '.join(DUNGEON_LAYOUT)}.")
        lineas.append("Mapa actualizado." if mapa_ok else
                      "⚠️ El estado se actualizó pero no se pudo dibujar la imagen del mapa.")
        return "\n".join(lineas)

    except Exception as e:
        return f"❌ Error al actualizar el mapa: {e}. El juego puede continuar."


# ---------------------------------------------------------------------------
# TOOLS 4 y 5: Vida del jugador (daño y curación sobre el estado real)
# ---------------------------------------------------------------------------

@tool
def damage_player(amount: int, source: str = "desconocido") -> str:
    """
    Resta puntos de vida al jugador en el estado real del juego.

    DEBES llamar esta herramienta cada vez que el jugador reciba daño
    (ataque enemigo, trampa, hechizo, caída), ANTES de narrar el resultado.
    Usa el daño final ya tirado con execute_dice_roll. No la uses si el ataque falla.

    Args:
        amount: Puntos de daño finales (entero positivo)
        source: Qué causó el daño (ej: "espectro", "trampa de pinchos")

    Returns:
        JSON con hp_anterior, nuevo_hp y si el jugador quedó inconsciente
    """
    try:
        from game_engine.state_manager import get_state_manager
        r = get_state_manager().damage_player(max(0, int(amount)), source)
        return _json({"hp_anterior": r["hp_anterior"], "danio": r["dano_recibido"], "fuente": source,
                      "nuevo_hp": r["nuevo_hp"], "hp_max": r["max_hp"], "inconsciente": r["muerto"]})
    except Exception as e:
        return _json({"error": f"No se pudo aplicar el daño: {e}"})


@tool
def heal_player(amount: int, source: str = "desconocido") -> str:
    """
    Suma puntos de vida al jugador en el estado real del juego.

    DEBES llamar esta herramienta cada vez que el jugador recupere vida
    (poción, hechizo, descanso), ANTES de narrar el resultado.

    Args:
        amount: Puntos de vida a recuperar (entero positivo)
        source: Qué causó la curación (ej: "poción de curación")

    Returns:
        JSON con hp_anterior, nuevo_hp y hp_max
    """
    try:
        from game_engine.state_manager import get_state_manager
        r = get_state_manager().heal_player(max(0, int(amount)), source)
        return _json({"hp_anterior": r["hp_anterior"], "curacion": r["curado"], "fuente": source,
                      "nuevo_hp": r["nuevo_hp"], "hp_max": r["max_hp"]})
    except Exception as e:
        return _json({"error": f"No se pudo aplicar la curación: {e}"})


# ---------------------------------------------------------------------------
# TOOL 6: Ficha del personaje
# ---------------------------------------------------------------------------

@tool
def set_character(name: str, race: str, char_class: str, level: int = 1) -> str:
    """
    Guarda la ficha del personaje cuando el jugador termina de crearlo.
    Llámala UNA sola vez, al tener nombre, raza y clase. El panel lateral y el informe
    final de la aventura usan estos datos.

    Args:
        name: Nombre del personaje
        race: Raza (ej: "elfo", "enano")
        char_class: Clase (ej: "mago", "guerrero")
        level: Nivel inicial (1 por defecto)

    Returns:
        JSON con la ficha guardada
    """
    try:
        from game_engine.state_manager import get_state_manager
        j = get_state_manager().set_character(name, race, char_class, level)
        return _json({"nombre": j["nombre"], "raza": j["raza"], "clase": j["clase"], "nivel": j["nivel"]})
    except Exception as e:
        return _json({"error": f"No se pudo guardar la ficha: {e}"})


# ---------------------------------------------------------------------------
# TOOL 7: Enemigos en combate
# ---------------------------------------------------------------------------

@tool
def manage_enemy(
    action: Literal["spawn", "damage", "remove"],
    name: str,
    hp: int = 0,
    amount: int = 0,
    xp: int = 0,
    gold: int = 0,
) -> str:
    """
    Lleva el estado de los enemigos del combate (se ven en el panel lateral).

    - spawn: un enemigo entra en combate. Indica name, hp (de query_rules_rag) y la
      recompensa: xp (SRD por CR: 1/4=50, 1/2=100, 1=200, 2=450, 3=700) y gold.
    - damage: el jugador hiere al enemigo name con amount (daño final ya tirado). Si su
      HP llega a 0 queda derrotado y el jugador recibe su oro y XP automáticamente.
    - remove: el enemigo huye o se rinde, sale del combate sin recompensa.

    Si hay varios del mismo tipo se numeran (Goblin, Goblin 2): usa el nombre exacto
    que devolvió spawn.

    Args:
        action: spawn, damage o remove
        name: Nombre del enemigo
        hp: Puntos de vida iniciales (solo spawn)
        amount: Daño final (solo damage)
        xp: XP que otorga al ser derrotado (solo spawn)
        gold: Oro que suelta al ser derrotado (solo spawn)

    Returns:
        JSON con el estado del enemigo; al derrotarlo incluye la recompensa y si el
        jugador subió de nivel.
    """
    try:
        from game_engine.state_manager import get_state_manager
        sm = get_state_manager()

        if action == "spawn":
            if hp <= 0:
                return _json({"error": "Para spawn hay que indicar hp > 0 (consúltalo en query_rules_rag)."})
            e = sm.spawn_enemy(name, hp, xp, gold)
            return _json({"accion": "spawn", "nombre": e["nombre"], "hp": e["hp"],
                          "recompensa_xp": e["xp"], "recompensa_oro": e["oro"]})

        npc_id = sm.find_npc(name)
        if npc_id is None:
            activos = [d["nombre"] for d in sm.get_active_npcs().values()]
            return _json({"error": f"No hay un enemigo llamado '{name}'.", "enemigos_activos": activos})

        if action == "damage":
            r = sm.damage_npc(npc_id, max(0, int(amount)))
            return _json({"accion": "damage", **r})

        if action == "remove":
            nombre = sm.get_active_npcs()[npc_id]["nombre"]
            sm.remove_npc(npc_id)
            sm.log_event("enemigo", f"{nombre} abandona el combate")
            return _json({"accion": "remove", "nombre": nombre})

        return _json({"error": f"Acción desconocida: {action}"})
    except Exception as e:
        return _json({"error": f"No se pudo gestionar el enemigo: {e}"})


# ---------------------------------------------------------------------------
# TOOL 8: Crónica de la aventura en PDF (resultado final tangible)
# ---------------------------------------------------------------------------

@tool
def generate_adventure_report(summary: str = "") -> str:
    """
    Genera la crónica de la aventura en PDF: portada, ficha del héroe, resumen, mapa con la
    ruta recorrida, cronología de sucesos, balance de combate y reglas consultadas.

    Úsala cuando el jugador pida la crónica, el informe o el resumen en PDF, o cuando
    decida terminar la aventura. Todos los datos salen del estado real del juego; tú solo
    aportas el epílogo narrativo.

    Args:
        summary: Epílogo de 3 a 6 frases, en español y sin markdown, que cierre la historia
                 del personaje (qué hizo, qué venció, cómo termina). Puede ir vacío.

    Returns:
        JSON con la ruta del PDF generado (pdf), su cantidad de páginas y su tamaño en KB.
    """
    try:
        from game_engine.state_manager import get_state_manager
        from game_engine.map_renderer import get_map_renderer
        from game_engine.report import build_report

        sm = get_state_manager()
        # El mapa se vuelve a dibujar para que el informe muestre el estado final
        mapa = get_map_renderer().render_fog_of_war(discovered_rooms=sm.get_discovered_rooms())
        r = build_report(summary=summary, ruta_mapa=mapa)
        return _json({"pdf": r["pdf"], "paginas": r["paginas"], "sucesos": r["eventos"], "kb": r["kb"]})
    except Exception as e:
        return _json({"error": f"No se pudo generar el PDF: {e}"})
