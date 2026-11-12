"""
state_manager.py - Gestor de Estado del Juego
Maneja el estado persistente de la partida en un archivo JSON.
"""
import json
import os
import pathlib
import threading
from datetime import datetime

# Ruta al archivo de estado del juego
STATE_FILE = "storage/game_state.json"

# Estado por defecto cuando se inicia una nueva partida
DEFAULT_STATE = {
    "jugador": {
        "nombre": "Aventurero",
        "clase": "Guerrero",
        "nivel": 1,
        "hp": 100,
        "max_hp": 100,
        "oro": 50,
        "experiencia": 0
    },
    "npcs": {},
    "salas_descubiertas": ["sala_entrada"],
    "sesion_iniciada": datetime.now().isoformat()
}


class StateManager:
    """
    Gestor de estado del juego con patrón Singleton.
    Maneja la persistencia del estado en un archivo JSON con soporte
    para acceso concurrente mediante locks de threading.
    """

    # Instancia única del Singleton
    _instance = None
    _lock_singleton = threading.Lock()

    def __new__(cls):
        """Implementación del patrón Singleton."""
        with cls._lock_singleton:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """
        Inicializa el StateManager.
        Solo se ejecuta una vez gracias al patrón Singleton.
        """
        # Evitar re-inicialización en llamadas subsiguientes
        if hasattr(self, '_initialized'):
            return

        # Lock para operaciones de lectura/escritura concurrentes
        self._lock = threading.Lock()
        # Inicializar el estado del juego
        self._init_state()
        self._initialized = True

    def _init_state(self):
        """
        Inicializa el estado del juego.
        Si el archivo de estado no existe, lo crea con valores por defecto.
        Si existe, simplemente lo verifica para asegurarse de que sea válido.
        """
        # Crear directorios necesarios si no existen
        state_path = pathlib.Path(STATE_FILE)
        state_path.parent.mkdir(parents=True, exist_ok=True)

        if not state_path.exists():
            # El archivo no existe: crear con estado por defecto
            estado_inicial = DEFAULT_STATE.copy()
            estado_inicial["sesion_iniciada"] = datetime.now().isoformat()
            self.save_state(estado_inicial)
        else:
            # El archivo existe: verificar que sea JSON válido
            try:
                self.load_state()
            except (json.JSONDecodeError, KeyError):
                # Archivo corrupto: resetear al estado por defecto
                estado_inicial = DEFAULT_STATE.copy()
                estado_inicial["sesion_iniciada"] = datetime.now().isoformat()
                self.save_state(estado_inicial)

    def load_state(self) -> dict:
        """
        Carga el estado del juego desde el archivo JSON.
        Usa un lock para garantizar acceso seguro en entornos concurrentes.

        Returns:
            dict: El estado actual del juego.
        """
        with self._lock:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)

    def save_state(self, state: dict):
        """
        Guarda el estado del juego en el archivo JSON.
        Usa un lock para garantizar escritura segura.

        Args:
            state (dict): El estado del juego a guardar.
        """
        with self._lock:
            state_path = pathlib.Path(STATE_FILE)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

    # -------------------------------------------------------------------------
    # Métodos del Jugador
    # -------------------------------------------------------------------------

    def get_player_hp(self) -> int:
        """
        Obtiene los puntos de vida actuales del jugador.

        Returns:
            int: HP actual del jugador.
        """
        state = self.load_state()
        return state["jugador"]["hp"]

    def get_player_max_hp(self) -> int:
        """
        Obtiene los puntos de vida máximos del jugador.

        Returns:
            int: HP máximo del jugador.
        """
        state = self.load_state()
        return state["jugador"]["max_hp"]

    def get_player_info(self) -> dict:
        """
        Obtiene toda la información del jugador.

        Returns:
            dict: Diccionario con todos los datos del jugador.
        """
        state = self.load_state()
        return state["jugador"]

    def set_player_hp(self, hp: int):
        """
        Establece los puntos de vida del jugador con límites entre 0 y max_hp.

        Args:
            hp (int): Nuevo valor de HP del jugador.
        """
        state = self.load_state()
        max_hp = state["jugador"]["max_hp"]
        # Clamp: asegurarse de que el HP esté entre 0 y max_hp
        state["jugador"]["hp"] = max(0, min(hp, max_hp))
        self.save_state(state)

    def damage_player(self, damage: int) -> dict:
        """
        Aplica daño al jugador y retorna el resultado.

        Args:
            damage (int): Cantidad de daño a aplicar.

        Returns:
            dict: Resultado con nuevo_hp, dano_recibido y si el jugador murió.
        """
        state = self.load_state()
        hp_anterior = state["jugador"]["hp"]
        # Asegurarse de que el daño no sea negativo
        damage = max(0, damage)
        nuevo_hp = max(0, hp_anterior - damage)
        state["jugador"]["hp"] = nuevo_hp
        self.save_state(state)

        return {
            "nuevo_hp": nuevo_hp,
            "dano_recibido": damage,
            "muerto": nuevo_hp <= 0
        }

    def heal_player(self, amount: int) -> dict:
        """
        Cura al jugador y retorna el resultado.

        Args:
            amount (int): Cantidad de HP a restaurar.

        Returns:
            dict: Resultado con nuevo_hp y cantidad realmente curada.
        """
        state = self.load_state()
        hp_anterior = state["jugador"]["hp"]
        max_hp = state["jugador"]["max_hp"]
        # Asegurarse de que la curación no sea negativa
        amount = max(0, amount)
        nuevo_hp = min(max_hp, hp_anterior + amount)
        # La curación real puede ser menor si ya estaba casi lleno
        curado_real = nuevo_hp - hp_anterior
        state["jugador"]["hp"] = nuevo_hp
        self.save_state(state)

        return {
            "nuevo_hp": nuevo_hp,
            "curado": curado_real
        }

    # -------------------------------------------------------------------------
    # Métodos de NPCs (Personajes No Jugables)
    # -------------------------------------------------------------------------

    def add_npc(self, npc_id: str, name: str, hp: int, max_hp: int = None):
        """
        Agrega un nuevo NPC al estado del juego.

        Args:
            npc_id (str): Identificador único del NPC.
            name (str): Nombre del NPC.
            hp (int): Puntos de vida iniciales del NPC.
            max_hp (int, optional): HP máximo. Si es None, se usa el valor de hp.
        """
        state = self.load_state()
        # Si no se especifica max_hp, usar el valor inicial de hp
        if max_hp is None:
            max_hp = hp
        state["npcs"][npc_id] = {
            "nombre": name,
            "hp": hp,
            "max_hp": max_hp
        }
        self.save_state(state)

    def remove_npc(self, npc_id: str):
        """
        Elimina un NPC del estado del juego.

        Args:
            npc_id (str): Identificador del NPC a eliminar.
        """
        state = self.load_state()
        if npc_id in state["npcs"]:
            del state["npcs"][npc_id]
            self.save_state(state)

    def get_npc_hp(self, npc_id: str) -> int:
        """
        Obtiene los puntos de vida de un NPC.

        Args:
            npc_id (str): Identificador del NPC.

        Returns:
            int: HP del NPC, o -1 si el NPC no existe.
        """
        state = self.load_state()
        if npc_id in state["npcs"]:
            return state["npcs"][npc_id]["hp"]
        return -1

    def set_npc_hp(self, npc_id: str, hp: int):
        """
        Establece los puntos de vida de un NPC.

        Args:
            npc_id (str): Identificador del NPC.
            hp (int): Nuevo valor de HP.
        """
        state = self.load_state()
        if npc_id in state["npcs"]:
            max_hp = state["npcs"][npc_id]["max_hp"]
            state["npcs"][npc_id]["hp"] = max(0, min(hp, max_hp))
            self.save_state(state)

    def damage_npc(self, npc_id: str, damage: int) -> dict:
        """
        Aplica daño a un NPC. Si el HP llega a 0, el NPC es eliminado.

        Args:
            npc_id (str): Identificador del NPC.
            damage (int): Cantidad de daño a aplicar.

        Returns:
            dict: Resultado del daño con nuevo_hp, dano_recibido, eliminado y estado.
        """
        state = self.load_state()

        # Verificar si el NPC existe
        if npc_id not in state["npcs"]:
            return {
                "error": f"NPC '{npc_id}' no encontrado",
                "nuevo_hp": -1,
                "dano_recibido": 0,
                "eliminado": False
            }

        hp_anterior = state["npcs"][npc_id]["hp"]
        damage = max(0, damage)
        nuevo_hp = max(0, hp_anterior - damage)
        eliminado = nuevo_hp <= 0

        if eliminado:
            # Eliminar el NPC del estado si fue derrotado
            nombre_npc = state["npcs"][npc_id]["nombre"]
            del state["npcs"][npc_id]
            self.save_state(state)
            return {
                "nuevo_hp": 0,
                "dano_recibido": damage,
                "eliminado": True,
                "mensaje": f"{nombre_npc} ha sido derrotado!"
            }
        else:
            state["npcs"][npc_id]["hp"] = nuevo_hp
            self.save_state(state)
            return {
                "nuevo_hp": nuevo_hp,
                "dano_recibido": damage,
                "eliminado": False
            }

    def get_active_npcs(self) -> dict:
        """
        Obtiene todos los NPCs activos en el juego.

        Returns:
            dict: Diccionario con todos los NPCs activos y sus datos.
        """
        state = self.load_state()
        return state["npcs"]

    # -------------------------------------------------------------------------
    # Métodos del Mapa / Exploración
    # -------------------------------------------------------------------------

    def discover_room(self, room_id: str):
        """
        Marca una sala como descubierta por el jugador.

        Args:
            room_id (str): Identificador de la sala descubierta.
        """
        state = self.load_state()
        if room_id not in state["salas_descubiertas"]:
            state["salas_descubiertas"].append(room_id)
            self.save_state(state)

    def get_discovered_rooms(self) -> list:
        """
        Obtiene la lista de salas descubiertas por el jugador.

        Returns:
            list: Lista de identificadores de salas descubiertas.
        """
        state = self.load_state()
        return state["salas_descubiertas"]

    # -------------------------------------------------------------------------
    # Métodos Generales
    # -------------------------------------------------------------------------

    def get_state(self) -> dict:
        """
        Alias de get_full_state() para compatibilidad con la interfaz.
        Retorna el estado completo del juego.
        """
        return self.load_state()

    def get_full_state(self) -> dict:
        """
        Obtiene el estado completo del juego.

        Returns:
            dict: Estado completo del juego incluyendo jugador, NPCs y mapa.
        """
        return self.load_state()

    def reset_state(self):
        """
        Resetea el estado del juego a los valores por defecto.
        Elimina el archivo de estado existente y crea uno nuevo.
        """
        state_path = pathlib.Path(STATE_FILE)
        # Eliminar archivo existente si existe
        if state_path.exists():
            os.remove(STATE_FILE)
        # Crear nuevo estado con valores por defecto
        self._init_state()


    # -------------------------------------------------------------------------
    # Métodos alias para compatibilidad
    # -------------------------------------------------------------------------

    def reveal_room(self, room_id: str) -> bool:
        """
        Alias de discover_room. Revela una sala y retorna True si era nueva.

        Args:
            room_id (str): Identificador de la sala a revelar.

        Returns:
            bool: True si la sala era nueva, False si ya estaba descubierta.
        """
        previas = self.get_discovered_rooms()
        self.discover_room(room_id)
        return room_id not in previas

    def get_revealed_rooms(self) -> list:
        """Alias de get_discovered_rooms para compatibilidad."""
        return self.get_discovered_rooms()

    def get_current_room(self) -> str:
        """
        Retorna el ID de la sala actual del jugador (última sala descubierta).

        Returns:
            str: ID de la sala actual, o 'sala_entrada' por defecto.
        """
        rooms = self.get_discovered_rooms()
        return rooms[-1] if rooms else "sala_entrada"


def get_state_manager() -> StateManager:
    """
    Función de acceso al Singleton de StateManager.

    Returns:
        StateManager: La instancia única del gestor de estado.
    """
    return StateManager()
