"""
state_manager.py - Gestor de Estado del Juego

Estado persistente de la partida en un archivo JSON (jugador, enemigos, mapa y
registro de eventos).

Concurrencia: LangGraph puede ejecutar varias tools EN PARALELO dentro de un mismo
turno (Gemini lo hace), y cada una modifica este estado. Por eso toda modificación
es una transacción atómica: leer, cambiar y guardar ocurren bajo un único lock
(reentrante). Sin eso, dos tools simultáneas pisarían los cambios de la otra.
"""
import copy
import json
import os
import pathlib
import re
import threading
from contextlib import contextmanager
from datetime import datetime

# Ruta al archivo de estado del juego
STATE_FILE = "storage/game_state.json"

# Máximo de eventos que se conservan en el registro (los más recientes)
MAX_EVENTOS = 300

# XP acumulada necesaria para alcanzar cada nivel (D&D 5e): nivel n -> XP_NIVELES[n - 1]
XP_NIVELES = [0, 300, 900, 2700, 6500, 14000, 23000, 34000, 48000, 64000]

# Estado por defecto cuando se inicia una nueva partida
DEFAULT_STATE = {
    "jugador": {
        "nombre": "Aventurero",
        "raza": "",
        "clase": "Guerrero",
        "nivel": 1,
        "hp": 100,
        "max_hp": 100,
        "oro": 50,
        "experiencia": 0,
        "ficha_creada": False,   # True cuando el jugador completó su personaje (set_character)
    },
    "npcs": {},
    "salas_descubiertas": ["sala_entrada"],
    "sala_actual": "sala_entrada",
    "ruta": ["sala_entrada"],     # salas en el orden en que se visitaron (con regresos)
    "eventos": [],                # registro de la partida: {"t", "tipo", "texto"}
    "sesion_iniciada": "",
}


def _estado_inicial() -> dict:
    """Copia profunda del estado por defecto, con la hora de inicio."""
    estado = copy.deepcopy(DEFAULT_STATE)
    estado["sesion_iniciada"] = datetime.now().isoformat()
    return estado


def _migrar(state: dict) -> dict:
    """
    Completa las claves que falten. Los archivos de estado creados por versiones
    anteriores (que sobreviven a un `git pull`) no tienen las claves nuevas.
    """
    base = DEFAULT_STATE
    jugador = state.setdefault("jugador", copy.deepcopy(base["jugador"]))
    for clave, valor in base["jugador"].items():
        jugador.setdefault(clave, valor)
    state.setdefault("npcs", {})
    salas = state.setdefault("salas_descubiertas", ["sala_entrada"])
    state.setdefault("sala_actual", salas[-1] if salas else "sala_entrada")
    state.setdefault("ruta", [state["sala_actual"]])
    state.setdefault("eventos", [])
    state.setdefault("sesion_iniciada", datetime.now().isoformat())
    return state


def _nivel_por_xp(xp: int) -> int:
    """Nivel que corresponde a una cantidad de XP acumulada."""
    nivel = 1
    for n, minimo in enumerate(XP_NIVELES, 1):
        if xp >= minimo:
            nivel = n
    return nivel


def _slug(texto: str) -> str:
    """Identificador estable a partir de un nombre ('Goblin Jefe' -> 'goblin_jefe')."""
    return re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_") or "enemigo"


class StateManager:
    """
    Gestor de estado del juego con patrón Singleton.
    Toda escritura pasa por `transaccion()`, que es atómica entre hilos.
    """

    _instance = None
    _lock_singleton = threading.Lock()

    def __new__(cls):
        """Implementación del patrón Singleton."""
        with cls._lock_singleton:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Se ejecuta una sola vez gracias al Singleton."""
        if hasattr(self, "_initialized"):
            return
        # RLock: una transacción puede llamar a load_state/save_state sin bloquearse
        self._lock = threading.RLock()
        self._init_state()
        self._initialized = True

    def _init_state(self):
        """Crea el archivo de estado si no existe; si está corrupto, lo reinicia."""
        state_path = pathlib.Path(STATE_FILE)
        state_path.parent.mkdir(parents=True, exist_ok=True)

        if not state_path.exists():
            self.save_state(_estado_inicial())
            return
        try:
            self.load_state()
        except (json.JSONDecodeError, KeyError):
            self.save_state(_estado_inicial())

    # -------------------------------------------------------------------------
    # Persistencia y transacciones
    # -------------------------------------------------------------------------

    def load_state(self) -> dict:
        """Carga el estado desde el JSON (completando las claves nuevas si faltan)."""
        with self._lock:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return _migrar(json.load(f))

    def save_state(self, state: dict):
        """Guarda el estado en el JSON."""
        with self._lock:
            state_path = pathlib.Path(STATE_FILE)
            state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)

    @contextmanager
    def transaccion(self):
        """
        Lectura-modificación-escritura atómica:

            with sm.transaccion() as estado:
                estado["jugador"]["hp"] -= 5

        Mientras dura el bloque ningún otro hilo puede leer ni escribir el estado.
        """
        with self._lock:
            estado = self.load_state()
            yield estado
            self.save_state(estado)

    @staticmethod
    def _registrar(estado: dict, tipo: str, texto: str):
        """Agrega un evento al registro (dentro de una transacción ya abierta)."""
        estado["eventos"].append({"t": datetime.now().isoformat(timespec="seconds"), "tipo": tipo, "texto": texto})
        del estado["eventos"][:-MAX_EVENTOS]

    def log_event(self, tipo: str, texto: str):
        """Registra un evento de la partida (tiradas, consultas al reglamento, etc.)."""
        with self.transaccion() as estado:
            self._registrar(estado, tipo, texto)

    def get_events(self, limite: int = 0) -> list:
        """Eventos de la partida en orden cronológico (los últimos `limite` si se indica)."""
        eventos = self.load_state()["eventos"]
        return eventos[-limite:] if limite else eventos

    # -------------------------------------------------------------------------
    # Jugador
    # -------------------------------------------------------------------------

    def get_player_hp(self) -> int:
        """HP actual del jugador."""
        return self.load_state()["jugador"]["hp"]

    def get_player_max_hp(self) -> int:
        """HP máximo del jugador."""
        return self.load_state()["jugador"]["max_hp"]

    def get_player_info(self) -> dict:
        """Todos los datos del jugador."""
        return self.load_state()["jugador"]

    def set_player_hp(self, hp: int):
        """Fija el HP del jugador (entre 0 y max_hp)."""
        with self.transaccion() as estado:
            j = estado["jugador"]
            j["hp"] = max(0, min(hp, j["max_hp"]))

    def damage_player(self, damage: int, source: str = "") -> dict:
        """Aplica daño al jugador y lo registra. Devuelve hp_anterior, nuevo_hp y si murió."""
        with self.transaccion() as estado:
            j = estado["jugador"]
            hp_anterior = j["hp"]
            damage = max(0, damage)
            j["hp"] = max(0, hp_anterior - damage)
            self._registrar(
                estado, "danio",
                f"El jugador recibe {damage} de daño" + (f" ({source})" if source else "")
                + f": {hp_anterior} → {j['hp']} HP",
            )
            return {"hp_anterior": hp_anterior, "nuevo_hp": j["hp"], "dano_recibido": damage,
                    "max_hp": j["max_hp"], "muerto": j["hp"] <= 0}

    def heal_player(self, amount: int, source: str = "") -> dict:
        """Cura al jugador y lo registra. Devuelve hp_anterior, nuevo_hp y lo curado."""
        with self.transaccion() as estado:
            j = estado["jugador"]
            hp_anterior = j["hp"]
            j["hp"] = min(j["max_hp"], hp_anterior + max(0, amount))
            self._registrar(
                estado, "curacion",
                "El jugador recupera vida" + (f" ({source})" if source else "")
                + f": {hp_anterior} → {j['hp']} HP",
            )
            return {"hp_anterior": hp_anterior, "nuevo_hp": j["hp"],
                    "curado": j["hp"] - hp_anterior, "max_hp": j["max_hp"]}

    def set_character(self, nombre: str, raza: str, clase: str, nivel: int = 1) -> dict:
        """Guarda la ficha del personaje (lo que el jugador eligió al crearlo)."""
        with self.transaccion() as estado:
            j = estado["jugador"]
            j.update({"nombre": nombre.strip() or j["nombre"], "raza": raza.strip(),
                      "clase": clase.strip() or j["clase"], "nivel": max(1, int(nivel)),
                      "ficha_creada": True})
            self._registrar(estado, "personaje",
                            f"Nace {j['nombre']}, {j['raza']} {j['clase']} de nivel {j['nivel']}")
            return dict(j)

    def _dar_recompensa(self, estado: dict, oro: int, xp: int) -> dict:
        """Suma oro y XP al jugador (dentro de una transacción) y sube de nivel si corresponde."""
        j = estado["jugador"]
        nivel_anterior = j["nivel"]
        j["oro"] += max(0, oro)
        j["experiencia"] += max(0, xp)
        j["nivel"] = max(j["nivel"], _nivel_por_xp(j["experiencia"]))
        if j["nivel"] > nivel_anterior:
            self._registrar(estado, "nivel", f"¡{j['nombre']} sube al nivel {j['nivel']}!")
        return {"oro": oro, "xp": xp, "nivel": j["nivel"], "nivel_subio": j["nivel"] > nivel_anterior}

    # -------------------------------------------------------------------------
    # Enemigos (NPCs)
    # -------------------------------------------------------------------------

    def add_npc(self, npc_id: str, name: str, hp: int, max_hp: int = None, xp: int = 0, oro: int = 0):
        """Agrega un enemigo con sus puntos de vida y la recompensa que da al ser derrotado."""
        with self.transaccion() as estado:
            estado["npcs"][npc_id] = {"nombre": name, "hp": hp, "max_hp": max_hp or hp,
                                      "xp": max(0, xp), "oro": max(0, oro)}

    def spawn_enemy(self, nombre: str, hp: int, xp: int = 0, oro: int = 0) -> dict:
        """
        Crea un enemigo con identificador y nombre únicos ('Goblin', 'Goblin 2', ...),
        para poder tener varios del mismo tipo en combate.
        """
        with self.transaccion() as estado:
            npcs = estado["npcs"]
            base, n = nombre.strip() or "Enemigo", 1
            nombre_final = base
            while any(d["nombre"].lower() == nombre_final.lower() for d in npcs.values()):
                n += 1
                nombre_final = f"{base} {n}"
            npc_id = _slug(nombre_final)
            hp = max(1, int(hp))
            npcs[npc_id] = {"nombre": nombre_final, "hp": hp, "max_hp": hp,
                            "xp": max(0, int(xp)), "oro": max(0, int(oro))}
            self._registrar(estado, "enemigo", f"Aparece {nombre_final} ({hp} HP)")
            return {"id": npc_id, **npcs[npc_id]}

    def find_npc(self, referencia: str):
        """Busca un enemigo por id o por nombre (sin distinguir mayúsculas). None si no existe."""
        ref = referencia.strip().lower()
        for npc_id, datos in self.load_state()["npcs"].items():
            if ref in (npc_id.lower(), datos["nombre"].lower()):
                return npc_id
        return None

    def remove_npc(self, npc_id: str):
        """Elimina un enemigo (huyó, se rindió...)."""
        with self.transaccion() as estado:
            estado["npcs"].pop(npc_id, None)

    def get_npc_hp(self, npc_id: str) -> int:
        """HP de un enemigo, o -1 si no existe."""
        npc = self.load_state()["npcs"].get(npc_id)
        return npc["hp"] if npc else -1

    def set_npc_hp(self, npc_id: str, hp: int):
        """Fija el HP de un enemigo (entre 0 y su máximo)."""
        with self.transaccion() as estado:
            npc = estado["npcs"].get(npc_id)
            if npc:
                npc["hp"] = max(0, min(hp, npc["max_hp"]))

    def damage_npc(self, npc_id: str, damage: int) -> dict:
        """
        Aplica daño a un enemigo. Si su HP llega a 0 queda derrotado, sale del combate y
        el jugador recibe su recompensa de oro y XP (todo en la misma transacción).
        """
        with self.transaccion() as estado:
            npc = estado["npcs"].get(npc_id)
            if npc is None:
                return {"error": f"Enemigo '{npc_id}' no encontrado", "nuevo_hp": -1,
                        "dano_recibido": 0, "eliminado": False}

            hp_anterior = npc["hp"]
            damage = max(0, damage)
            nuevo_hp = max(0, hp_anterior - damage)
            if nuevo_hp > 0:
                npc["hp"] = nuevo_hp
                self._registrar(estado, "enemigo",
                                f"{npc['nombre']} recibe {damage} de daño: {hp_anterior} → {nuevo_hp} HP")
                return {"nombre": npc["nombre"], "hp_anterior": hp_anterior, "nuevo_hp": nuevo_hp,
                        "max_hp": npc["max_hp"], "dano_recibido": damage, "eliminado": False}

            # Derrotado: sale del combate y entrega su recompensa
            del estado["npcs"][npc_id]
            recompensa = self._dar_recompensa(estado, npc.get("oro", 0), npc.get("xp", 0))
            self._registrar(estado, "enemigo",
                            f"{npc['nombre']} es derrotado (+{recompensa['xp']} XP, +{recompensa['oro']} oro)")
            return {"nombre": npc["nombre"], "hp_anterior": hp_anterior, "nuevo_hp": 0,
                    "max_hp": npc["max_hp"], "dano_recibido": damage, "eliminado": True,
                    "recompensa": recompensa}

    def get_active_npcs(self) -> dict:
        """Enemigos activos y sus datos."""
        return self.load_state()["npcs"]

    # -------------------------------------------------------------------------
    # Mapa / exploración
    # -------------------------------------------------------------------------

    def discover_room(self, room_id: str):
        """Marca una sala como descubierta."""
        with self.transaccion() as estado:
            if room_id not in estado["salas_descubiertas"]:
                estado["salas_descubiertas"].append(room_id)

    def move_to_room(self, room_id: str, nombre: str = "") -> bool:
        """
        Pone al jugador en una sala: la descubre, la fija como sala actual y la suma a la
        ruta si cambió. Devuelve True si la sala era nueva.
        """
        with self.transaccion() as estado:
            era_nueva = room_id not in estado["salas_descubiertas"]
            if era_nueva:
                estado["salas_descubiertas"].append(room_id)
            if estado["sala_actual"] != room_id:
                estado["sala_actual"] = room_id
                estado["ruta"].append(room_id)
                self._registrar(estado, "mapa", f"El grupo llega a {nombre or room_id}"
                                + (" (sala nueva)" if era_nueva else ""))
            return era_nueva

    def get_discovered_rooms(self) -> list:
        """Salas descubiertas por el jugador."""
        return self.load_state()["salas_descubiertas"]

    def get_current_room(self) -> str:
        """ID de la sala donde está el jugador ahora."""
        return self.load_state()["sala_actual"]

    def get_route(self) -> list:
        """Salas recorridas, en orden (incluye los regresos)."""
        return self.load_state()["ruta"]

    # -------------------------------------------------------------------------
    # Generales
    # -------------------------------------------------------------------------

    def get_state(self) -> dict:
        """Estado completo del juego."""
        return self.load_state()

    def get_full_state(self) -> dict:
        """Alias de get_state()."""
        return self.load_state()

    def reset_state(self):
        """Reinicia la partida: borra el archivo de estado y crea uno nuevo por defecto."""
        with self._lock:
            state_path = pathlib.Path(STATE_FILE)
            if state_path.exists():
                os.remove(STATE_FILE)
            self._init_state()

    # Alias de compatibilidad
    def reveal_room(self, room_id: str) -> bool:
        """Alias de discover_room(): True si la sala era nueva."""
        previas = self.get_discovered_rooms()
        self.discover_room(room_id)
        return room_id not in previas

    def get_revealed_rooms(self) -> list:
        """Alias de get_discovered_rooms()."""
        return self.get_discovered_rooms()


def get_state_manager() -> StateManager:
    """Acceso al Singleton de StateManager."""
    return StateManager()
