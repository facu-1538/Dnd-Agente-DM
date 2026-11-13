"""
state_manager.py - Gestor de Estado del Juego (con partidas guardadas)

Cada PARTIDA vive en su propia carpeta, storage/partidas/<id>/:

    estado.json     ficha, vida, enemigos, mapa, ruta y registro de eventos
    mensajes.json   historial visible del chat (lo que muestra la interfaz)
    mapa.png        el mapa actual de esa partida
    informes/       las crónicas en PDF de esa partida

La memoria de la conversación del agente NO está acá: la guarda el checkpointer (SQLite) usando
el id de la partida como thread_id (ver agent/core.py). Un solo id gobierna los tres almacenes.

Límite conocido: el estado es un singleton del proceso con UNA partida activa a la vez (pensado para
un jugador). La interfaz reapunta el estado a la partida de la sesión antes de cada turno.

Concurrencia: LangGraph puede ejecutar varias tools EN PARALELO dentro de un mismo turno (Gemini lo
hace), y cada una modifica este estado. Por eso toda modificación es una transacción atómica: leer,
cambiar y guardar ocurren bajo un único lock (reentrante). Sin eso, dos tools simultáneas pisarían los
cambios de la otra.
"""
import copy
import json
import os
import pathlib
import re
import secrets
import shutil
import threading
from contextlib import contextmanager
from datetime import datetime

# Carpeta con todas las partidas. Se puede cambiar con DM_PARTIDAS_DIR (las pruebas usan una temporal).
PARTIDAS_DIR = pathlib.Path(os.getenv("DM_PARTIDAS_DIR", "storage/partidas"))



def _legacy_file() -> pathlib.Path:
    """
    Archivo de estado de versiones anteriores (una sola partida global). Se busca JUNTO a la carpeta de
    partidas (en producción, storage/game_state.json): si existe y todavía no hay partidas, se importa
    como la primera para no perder lo que el jugador ya tenía. Con DM_PARTIDAS_DIR apuntando a una
    carpeta temporal (las pruebas), nunca puede tocar el archivo real.
    """
    return PARTIDAS_DIR.parent / "game_state.json"


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

_ID_VALIDO = re.compile(r"^[0-9a-z]+(-[0-9a-z]+)*$")


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


def _escribir_json(ruta: pathlib.Path, datos) -> None:
    """Escritura atómica: se escribe un temporal y se reemplaza, así un corte no deja un JSON a medias."""
    ruta.parent.mkdir(parents=True, exist_ok=True)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)
    os.replace(tmp, ruta)


class StateManager:
    """
    Gestor de estado del juego con patrón Singleton y partidas guardadas.
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
        self._id = None
        self._abrir_inicial()
        self._initialized = True

    # -------------------------------------------------------------------------
    # Partidas: carpeta, listado, apertura y borrado
    # -------------------------------------------------------------------------

    @property
    def id_activa(self) -> str:
        """Id de la partida activa."""
        return self._id

    @property
    def dir_partida(self) -> pathlib.Path:
        return PARTIDAS_DIR / self._id

    @property
    def ruta_estado(self) -> pathlib.Path:
        return self.dir_partida / "estado.json"

    @property
    def ruta_mensajes(self) -> pathlib.Path:
        return self.dir_partida / "mensajes.json"

    @property
    def ruta_mapa(self) -> pathlib.Path:
        """Imagen del mapa de la partida activa."""
        return self.dir_partida / "mapa.png"

    @property
    def dir_informes(self) -> pathlib.Path:
        """Carpeta de las crónicas en PDF de la partida activa (se crea al pedirla)."""
        d = self.dir_partida / "informes"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _escribir_activa(partida_id: str) -> None:
        PARTIDAS_DIR.mkdir(parents=True, exist_ok=True)
        (PARTIDAS_DIR / ".activa").write_text(partida_id, encoding="utf-8")

    @staticmethod
    def _crear_carpeta(estado: dict = None) -> str:
        """Crea la carpeta de una partida nueva y devuelve su id (legible y ordenable por fecha)."""
        partida_id = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        _escribir_json(PARTIDAS_DIR / partida_id / "estado.json", estado if estado is not None else _estado_inicial())
        return partida_id

    def _abrir_inicial(self):
        """
        Elige la partida con la que arranca el proceso: la que estaba activa (así recargar la página
        no pierde nada), si no la más reciente, y si no existe ninguna, crea una nueva.
        Si venimos de una versión anterior, su partida única se importa primero.
        """
        PARTIDAS_DIR.mkdir(parents=True, exist_ok=True)
        existentes = self.listar_partidas()

        legacy = _legacy_file()
        if not existentes and legacy.exists():
            try:
                with open(legacy, "r", encoding="utf-8") as f:
                    self._id = self._crear_carpeta(_migrar(json.load(f)))
                legacy.replace(legacy.with_suffix(".json.migrado"))
                self._escribir_activa(self._id)
                return
            except (OSError, json.JSONDecodeError):
                pass                                  # estado viejo ilegible: se empieza de cero

        activa_file = PARTIDAS_DIR / ".activa"
        if activa_file.exists():
            activa = activa_file.read_text(encoding="utf-8").strip()
            if _ID_VALIDO.match(activa) and (PARTIDAS_DIR / activa / "estado.json").exists():
                self._id = activa
                return
        if existentes:
            self._id = existentes[0]["id"]
        else:
            self._id = self._crear_carpeta()
        self._escribir_activa(self._id)

    def nueva_partida(self) -> str:
        """Crea una partida nueva, la deja activa y devuelve su id. Las anteriores NO se tocan."""
        with self._lock:
            self._id = self._crear_carpeta()
            self._escribir_activa(self._id)
            return self._id

    def abrir_partida(self, partida_id: str) -> bool:
        """Activa una partida existente. Devuelve False si el id no es válido o no existe."""
        if not isinstance(partida_id, str) or not _ID_VALIDO.match(partida_id):
            return False
        with self._lock:
            if self._id == partida_id:
                return True
            if not (PARTIDAS_DIR / partida_id / "estado.json").exists():
                return False
            self._id = partida_id
            self._escribir_activa(partida_id)
            return True

    def borrar_partida(self, partida_id: str) -> str:
        """
        Borra una partida (su estado, historial, mapa e informes). Si era la activa, pasa a la más
        reciente de las que quedan, o crea una nueva si no queda ninguna. Devuelve el id activo.
        La memoria de la conversación del agente la borra quien llama (core.clear_conversation).
        """
        with self._lock:
            if _ID_VALIDO.match(partida_id or "") and (PARTIDAS_DIR / partida_id).is_dir():
                shutil.rmtree(PARTIDAS_DIR / partida_id, ignore_errors=True)
            if self._id == partida_id or not (PARTIDAS_DIR / (self._id or "") / "estado.json").exists():
                restantes = self.listar_partidas()
                self._id = restantes[0]["id"] if restantes else self._crear_carpeta()
                self._escribir_activa(self._id)
            return self._id

    def listar_partidas(self) -> list:
        """
        Partidas guardadas, la más reciente primero. Cada una: id, nombre, raza, clase, nivel,
        ficha_creada, salas, turnos (mensajes del jugador), actualizado (datetime) y activa.
        """
        partidas = []
        if not PARTIDAS_DIR.exists():
            return partidas
        for carpeta in PARTIDAS_DIR.iterdir():
            archivo = carpeta / "estado.json"
            if not carpeta.is_dir() or not archivo.exists():
                continue
            try:
                with open(archivo, "r", encoding="utf-8") as f:
                    estado = _migrar(json.load(f))
                mtime = archivo.stat().st_mtime
            except (OSError, json.JSONDecodeError):
                continue                               # estado dañado: la carpeta no rompe el listado
            # El historial se lee aparte: si está dañado la partida SIGUE apareciendo (con 0 turnos)
            turnos = 0
            msgs = carpeta / "mensajes.json"
            if msgs.exists():
                try:
                    with open(msgs, "r", encoding="utf-8") as f:
                        turnos = sum(1 for m in json.load(f) if isinstance(m, dict) and m.get("role") == "user")
                    mtime = max(mtime, msgs.stat().st_mtime)
                except (OSError, json.JSONDecodeError):
                    pass
            j = estado["jugador"]
            partidas.append({
                "id": carpeta.name, "nombre": j["nombre"], "raza": j["raza"], "clase": j["clase"],
                "nivel": j["nivel"], "ficha_creada": j["ficha_creada"],
                "salas": len(estado["salas_descubiertas"]), "turnos": turnos,
                "actualizado": datetime.fromtimestamp(mtime), "activa": carpeta.name == self._id,
            })
        return sorted(partidas, key=lambda p: p["actualizado"], reverse=True)

    # -------------------------------------------------------------------------
    # Historial visible del chat (mensajes.json)
    # -------------------------------------------------------------------------

    def guardar_mensajes(self, mensajes: list) -> None:
        """Guarda el historial visible de la partida activa (escritura atómica)."""
        with self._lock:
            _escribir_json(self.ruta_mensajes, mensajes)

    def cargar_mensajes(self) -> list:
        """Historial visible de la partida activa; lista vacía si no hay o está dañado."""
        with self._lock:
            try:
                with open(self.ruta_mensajes, "r", encoding="utf-8") as f:
                    datos = json.load(f)
                return datos if isinstance(datos, list) else []
            except (OSError, json.JSONDecodeError):
                return []

    # -------------------------------------------------------------------------
    # Persistencia y transacciones del estado de juego
    # -------------------------------------------------------------------------

    def load_state(self) -> dict:
        """Carga el estado de la partida activa (completando las claves nuevas si faltan)."""
        with self._lock:
            with open(self.ruta_estado, "r", encoding="utf-8") as f:
                return _migrar(json.load(f))

    def save_state(self, state: dict):
        """Guarda el estado de la partida activa."""
        with self._lock:
            _escribir_json(self.ruta_estado, state)

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
        """Devuelve la partida ACTIVA al estado inicial (ficha, vida, mapa y registro). No toca otras partidas."""
        with self._lock:
            self.save_state(_estado_inicial())

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
