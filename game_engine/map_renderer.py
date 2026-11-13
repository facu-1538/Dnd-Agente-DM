"""
map_renderer.py - Mapa ilustrado del dungeon con niebla de guerra
Dibuja el mapa como un pergamino con íconos por sala, ruta recorrida, posición del jugador,
brújula y leyenda, usando Pillow y las ilustraciones de assets/map/.
"""
import math
import pathlib
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# -------------------------------------------------------------------------
# Layout del Dungeon
# -------------------------------------------------------------------------

# Definición del mapa del dungeon con 8 salas
# Cada sala tiene: nombre, coordenadas (x,y) del centro en un canvas 800x600,
# conexiones a otras salas y una descripción temática
DUNGEON_LAYOUT = {
    "sala_entrada": {
        "nombre": "Entrada",
        "x": 400,
        "y": 300,
        "conexiones": ["sala_biblioteca", "sala_armeria", "sala_guardia"],
        "descripcion": "La entrada principal del dungeon. Antorchas iluminan las paredes de piedra."
    },
    "sala_biblioteca": {
        "nombre": "Biblioteca",
        "x": 400,
        "y": 150,
        "conexiones": ["sala_entrada", "sala_archivo"],
        "descripcion": "Estantes polvorientos repletos de antiguos tomos y pergaminos."
    },
    "sala_armeria": {
        "nombre": "Armería",
        "x": 400,
        "y": 450,
        "conexiones": ["sala_entrada", "sala_tesoro"],
        "descripcion": "Racks de armas oxidadas y armaduras abolladeras llenan esta sala."
    },
    "sala_guardia": {
        "nombre": "Sala Guardia",
        "x": 220,
        "y": 300,
        "conexiones": ["sala_entrada", "sala_mazmorra"],
        "descripcion": "Una antigua sala de guardia con una mesa de madera y sillas volcadas."
    },
    "sala_archivo": {
        "nombre": "Archivo",
        "x": 220,
        "y": 150,
        "conexiones": ["sala_biblioteca"],
        "descripcion": "Un archivo secreto con documentos sellados por cera negra."
    },
    "sala_tesoro": {
        "nombre": "Tesoro",
        "x": 580,
        "y": 450,
        "conexiones": ["sala_armeria", "sala_jefe"],
        "descripcion": "Cofres y monedas de oro brillan en esta cámara del tesoro."
    },
    "sala_mazmorra": {
        "nombre": "Mazmorra",
        "x": 220,
        "y": 450,
        "conexiones": ["sala_guardia"],
        "descripcion": "Celdas con barrotes oxidados. El sonido de cadenas resuena en la oscuridad."
    },
    "sala_jefe": {
        "nombre": "Trono del Jefe",
        "x": 580,
        "y": 300,
        "conexiones": ["sala_tesoro"],
        "descripcion": "Un imponente trono de obsidiana domina esta sala. El aire huele a azufre."
    }
}

# -------------------------------------------------------------------------
# Constantes de renderizado
# -------------------------------------------------------------------------

ASSETS_DIR = pathlib.Path("assets/map")
SALIDA_ACTUAL = "storage/maps/current_view.png"   # solo como respaldo: cada partida guarda su mapa en su carpeta
SALIDA_BASE = "storage/maps/base_dungeon.png"

# Lienzo de salida: la proporción 3:2 del pergamino ilustrado.
ANCHO, ALTO = 1536, 1024

# Las coordenadas del layout (canvas 800x600) se llevan al lienzo con UNA escala uniforme,
# centradas en (CX, CY): así los íconos conservan su proporción y las salas quedan separadas
# unos 285 px (un ícono mide 200 px de alto, más ~45 px del nombre debajo). Con estos valores
# la fila superior arranca en y=180 (debajo del título) y el último nombre termina en y=995;
# la columna derecha (x > 1250) queda libre para la brújula y la leyenda.
ESCALA = 1.9
CX, CY = 768, 565
ALTO_ICONO = 200

# Paleta (la misma de la interfaz y del PDF)
TINTA = (42, 35, 32)
ORO = (201, 162, 39)
ORO_CLARO = (236, 204, 118)
CARMESI = (142, 27, 45)
PERGAMINO = (236, 222, 184)
PASILLO_BORDE = (74, 48, 32)
PASILLO_PISO = (190, 160, 108)

# Sala -> ícono (assets/map/). Si falta alguno se dibuja una sala simple en su lugar.
ICONOS = {
    "sala_entrada": "icono_entrada.png",
    "sala_biblioteca": "icono_biblioteca.png",
    "sala_armeria": "icono_armeria.png",
    "sala_guardia": "icono_guardia.png",
    "sala_archivo": "icono_archivo.png",
    "sala_tesoro": "icono_tesoro.png",
    "sala_mazmorra": "icono_mazmorra.png",
    "sala_jefe": "icono_jefe.png",
}

# Fuentes con serifa, en orden de preferencia (Windows, Linux, macOS). Si ninguna existe se usa
# la fuente escalable que trae Pillow, así el mapa se ve parecido en cualquier máquina.
_SERIF_NEGRITA = ["georgiab.ttf", "Georgia Bold.ttf", "timesbd.ttf", "DejaVuSerif-Bold.ttf",
                  "LiberationSerif-Bold.ttf", "NotoSerif-Bold.ttf"]


def _fuente(size: int):
    """Fuente serif en negrita del tamaño pedido, con respaldo escalable de Pillow."""
    for nombre in _SERIF_NEGRITA:
        try:
            return ImageFont.truetype(nombre, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _punto(sala: dict) -> tuple:
    """Centro de una sala en el lienzo, a partir de sus coordenadas del layout."""
    return (round(CX + (sala["x"] - 400) * ESCALA), round(CY + (sala["y"] - 300) * ESCALA))


def _aristas() -> list:
    """Pasillos del dungeon como pares ordenados, sin repetir."""
    vistas = set()
    for sala_id, sala in DUNGEON_LAYOUT.items():
        for otra in sala["conexiones"]:
            if otra in DUNGEON_LAYOUT:
                vistas.add(tuple(sorted((sala_id, otra))))
    return sorted(vistas)


class MapRenderer:
    """
    Dibuja el mapa del dungeon como un pergamino ilustrado: íconos por sala, pasillos, la ruta
    recorrida (con el orden de los tramos), la posición actual del jugador y una niebla que
    oculta lo que todavía no se exploró, con una brújula y una leyenda.

    Los assets viven en assets/map/. Si falta alguno el mapa se sigue dibujando con una
    versión simple de esa pieza, nunca se rompe.
    """

    _instance = None

    def __new__(cls):
        """Singleton."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, "_initialized"):
            return
        pathlib.Path("storage/maps").mkdir(parents=True, exist_ok=True)
        self._cache = {}
        self._initialized = True

    # ------------------------------------------------------------------ assets

    def _asset(self, nombre: str):
        """Imagen RGBA de assets/map/ (se carga una sola vez), o None si no existe."""
        if nombre not in self._cache:
            ruta = ASSETS_DIR / nombre
            try:
                self._cache[nombre] = Image.open(ruta).convert("RGBA") if ruta.exists() else None
            except OSError:
                self._cache[nombre] = None
        return self._cache[nombre]

    @staticmethod
    def _cubrir(img: Image.Image, ancho: int, alto: int) -> Image.Image:
        """Escala y recorta para llenar el lienzo sin deformar ('cover')."""
        esc = max(ancho / img.width, alto / img.height)
        img = img.resize((round(img.width * esc), round(img.height * esc)), Image.LANCZOS)
        x, y = (img.width - ancho) // 2, (img.height - alto) // 2
        return img.crop((x, y, x + ancho, y + alto))

    def _fondo(self) -> Image.Image:
        fondo = self._asset("fondo_pergamino.jpg")
        if fondo is None:
            return Image.new("RGBA", (ANCHO, ALTO), PERGAMINO + (255,))
        return self._cubrir(fondo, ANCHO, ALTO)

    def _niebla(self) -> Image.Image:
        niebla = self._asset("niebla.jpg")
        if niebla is None:
            return Image.new("RGBA", (ANCHO, ALTO), (24, 20, 18, 255))
        return self._cubrir(niebla, ANCHO, ALTO)

    def _icono(self, sala_id: str):
        """Ícono de la sala escalado a ALTO_ICONO, o None si falta el archivo."""
        clave = f"escalado:{sala_id}"
        if clave not in self._cache:
            ico = self._asset(ICONOS.get(sala_id, ""))
            if ico is not None:
                esc = ALTO_ICONO / ico.height
                ico = ico.resize((round(ico.width * esc), ALTO_ICONO), Image.LANCZOS)
            self._cache[clave] = ico
        return self._cache[clave]

    # ----------------------------------------------------------------- dibujo

    @staticmethod
    def _pasillo(lienzo: Image.Image, a: tuple, b: tuple, tramo=None):
        """
        Pasillo de piedra entre dos salas: borde oscuro y piso claro, con un leve temblor
        (determinista, según la pareja de puntos) para que parezca dibujado a mano.
        `tramo=(desde, hasta)` dibuja solo ese segmento (en px, medidos desde `a`): se usa
        para el arranque de una salida sin explorar, que nace en el borde del ícono.
        """
        rng = random.Random(f"{a}{b}")
        largo = math.dist(a, b)
        s0, s1 = tramo if tramo else (0.0, largo)
        n = max(2, int((s1 - s0) / 40))
        ux, uy = (b[0] - a[0]) / largo, (b[1] - a[1]) / largo
        nx, ny = -uy, ux
        pts = []
        for i in range(n + 1):
            s = s0 + (s1 - s0) * i / n
            temblor = 0 if i in (0, n) else rng.uniform(-3, 3)
            pts.append((a[0] + ux * s + nx * temblor, a[1] + uy * s + ny * temblor))
        d = ImageDraw.Draw(lienzo)
        d.line(pts, fill=PASILLO_BORDE + (255,), width=36, joint="curve")
        d.line(pts, fill=PASILLO_PISO + (255,), width=24, joint="curve")

    @staticmethod
    def _discontinua(d: ImageDraw.ImageDraw, a, b, color, ancho=7, trazo=24, hueco=15):
        """Línea discontinua de a a b."""
        largo = math.dist(a, b)
        if largo == 0:
            return
        ux, uy = (b[0] - a[0]) / largo, (b[1] - a[1]) / largo
        pos = 0.0
        while pos < largo:
            fin = min(pos + trazo, largo)
            d.line((a[0] + ux * pos, a[1] + uy * pos, a[0] + ux * fin, a[1] + uy * fin), fill=color, width=ancho)
            pos += trazo + hueco

    def _sala(self, lienzo: Image.Image, sala_id: str, centro: tuple):
        """Pega el ícono de una sala con su sombra, o una sala simple si falta el asset."""
        ico = self._icono(sala_id)
        x, y = centro
        if ico is None:
            d = ImageDraw.Draw(lienzo)
            caja = (x - 95, y - 70, x + 95, y + 70)
            d.rounded_rectangle(caja, radius=14, fill=(196, 170, 120, 255), outline=PASILLO_BORDE + (255,), width=6)
            return
        sombra = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
        mascara = Image.new("L", lienzo.size, 0)
        mascara.paste(ico.getchannel("A").point(lambda v: int(v * 0.55)), (x - ico.width // 2 + 10, y - ico.height // 2 + 16))
        sombra.putalpha(mascara.filter(ImageFilter.GaussianBlur(11)))
        sombra_color = Image.new("RGBA", lienzo.size, (30, 20, 10, 255))
        sombra_color.putalpha(sombra.getchannel("A"))
        lienzo.alpha_composite(sombra_color)
        lienzo.alpha_composite(ico, (x - ico.width // 2, y - ico.height // 2))

    def _niebla_de_guerra(self, lienzo: Image.Image, descubiertas: set, todas_visibles: bool) -> Image.Image:
        """Oscurece lo no explorado con la textura de humo; deja claros las salas y pasillos vistos."""
        if todas_visibles:
            return lienzo
        mascara = Image.new("L", (ANCHO, ALTO), 255)           # 255 = niebla, 0 = despejado
        dm = ImageDraw.Draw(mascara)
        for sala_id in descubiertas:
            x, y = _punto(DUNGEON_LAYOUT[sala_id])
            dm.ellipse((x - 205, y - 175, x + 205, y + 175), fill=0)
        for a, b in _aristas():
            if a in descubiertas and b in descubiertas:
                dm.line((_punto(DUNGEON_LAYOUT[a]), _punto(DUNGEON_LAYOUT[b])), fill=0, width=150)
        mascara = mascara.filter(ImageFilter.GaussianBlur(46))
        niebla = self._niebla()
        niebla.putalpha(mascara.point(lambda v: int(v * 0.86)))
        return Image.alpha_composite(lienzo, niebla)

    def _marcador(self, tam: int) -> Image.Image:
        """Ficha del jugador (asset) o un círculo dorado simple como respaldo."""
        ficha = self._asset("marcador_jugador.png")
        if ficha is not None:
            return ficha.resize((tam, round(tam * ficha.height / ficha.width)), Image.LANCZOS)
        img = Image.new("RGBA", (tam, tam), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((2, 2, tam - 2, tam - 2), fill=CARMESI + (255,), outline=ORO + (255,), width=max(3, tam // 12))
        return img

    @staticmethod
    def _texto(d: ImageDraw.ImageDraw, xy, texto, fuente, relleno=ORO_CLARO, ancla="la", contorno=4):
        """Texto con contorno oscuro, legible tanto sobre pergamino como sobre niebla."""
        d.text(xy, texto, font=fuente, fill=relleno + (255,), anchor=ancla, stroke_width=contorno,
               stroke_fill=(24, 16, 10, 255))

    def _componer(self, descubiertas: set, actual, ruta: list, heroe: str, todas_visibles=False) -> Image.Image:
        lienzo = self._fondo()
        total = len(DUNGEON_LAYOUT)
        visibles = set(DUNGEON_LAYOUT) if todas_visibles else descubiertas

        # 1) Pasillos: completos entre salas vistas; un arranque corto hacia las no exploradas
        for a, b in _aristas():
            pa, pb = _punto(DUNGEON_LAYOUT[a]), _punto(DUNGEON_LAYOUT[b])
            if a in visibles and b in visibles:
                self._pasillo(lienzo, pa, pb)
            elif a in visibles:
                self._pasillo(lienzo, pa, pb, (ALTO_ICONO * 0.55, ALTO_ICONO * 0.55 + 85))
            elif b in visibles:
                self._pasillo(lienzo, pb, pa, (ALTO_ICONO * 0.55, ALTO_ICONO * 0.55 + 85))

        # 2) Ruta recorrida (por debajo de los íconos, que tapan sus extremos)
        puntos_ruta = [_punto(DUNGEON_LAYOUT[s]) for s in ruta if s in DUNGEON_LAYOUT]
        d = ImageDraw.Draw(lienzo)
        for a, b in zip(puntos_ruta, puntos_ruta[1:]):
            self._discontinua(d, a, b, CARMESI + (255,))

        # 3) Salas vistas
        for sala_id in sorted(visibles, key=lambda s: DUNGEON_LAYOUT[s]["y"]):
            self._sala(lienzo, sala_id, _punto(DUNGEON_LAYOUT[sala_id]))

        # 4) Niebla (los pasillos de arranque quedan bajo ella, como una pista tenue)
        lienzo = self._niebla_de_guerra(lienzo, descubiertas, todas_visibles)
        d = ImageDraw.Draw(lienzo)

        # 5) Salidas sin explorar: un "?" donde termina cada pasillo de arranque
        for sala_id, sala in DUNGEON_LAYOUT.items():
            if sala_id in visibles:
                continue
            if any(otra in visibles for otra in sala["conexiones"]):
                x, y = _punto(sala)
                self._texto(d, (x, y), "?", _fuente(104), ancla="mm", contorno=6)

        # 6) Números de la ruta (en el punto medio de cada tramo)
        usados = {}
        for orden, (sa, sb) in enumerate(zip(ruta, ruta[1:]), 1):
            if sa not in DUNGEON_LAYOUT or sb not in DUNGEON_LAYOUT:
                continue
            pa, pb = _punto(DUNGEON_LAYOUT[sa]), _punto(DUNGEON_LAYOUT[sb])
            par = tuple(sorted((sa, sb)))
            usados[par] = usados.get(par, 0) + 1
            largo = math.dist(pa, pb) or 1
            desplaza = (usados[par] - 1) * 40            # los regresos por el mismo pasillo no se pisan
            # El número va en el medio del tramo; en un pasillo vertical se corre hacia la sala de
            # abajo (60 %) para no tapar el nombre que está bajo la sala de arriba.
            t = 0.5
            if abs(pb[1] - pa[1]) > abs(pb[0] - pa[0]):
                t = 0.6 if pb[1] > pa[1] else 0.4
            mx = pa[0] + (pb[0] - pa[0]) * t - (pb[1] - pa[1]) / largo * desplaza
            my = pa[1] + (pb[1] - pa[1]) * t + (pb[0] - pa[0]) / largo * desplaza
            d.ellipse((mx - 19, my - 19, mx + 19, my + 19), fill=CARMESI + (255,), outline=ORO + (255,), width=3)
            d.text((mx, my), str(orden), font=_fuente(24), fill=(255, 255, 255, 255), anchor="mm")

        # 7) Posición actual: resplandor dorado y ficha del jugador
        if actual in visibles and actual in DUNGEON_LAYOUT:
            x, y = _punto(DUNGEON_LAYOUT[actual])
            brillo = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
            ImageDraw.Draw(brillo).ellipse((x - 160, y - 135, x + 160, y + 135), fill=ORO + (110,))
            lienzo.alpha_composite(brillo.filter(ImageFilter.GaussianBlur(32)))
            ficha = self._marcador(96)
            lienzo.alpha_composite(ficha, (x + 58, y - 112))

        # 8) Nombres de las salas vistas
        d = ImageDraw.Draw(lienzo)
        for sala_id in visibles:
            sala = DUNGEON_LAYOUT[sala_id]
            x, y = _punto(sala)
            self._texto(d, (x, y + ALTO_ICONO // 2 + 6), sala["nombre"], _fuente(31), ancla="ma")

        # 9) Título, brújula y leyenda
        self._texto(d, (70, 52), "Mapa de la mazmorra", _fuente(56), contorno=5)
        self._texto(d, (74, 122), f"{heroe}  ·  {len(descubiertas)} de {total} salas exploradas",
                    _fuente(27), relleno=PERGAMINO, contorno=3)
        self._brujula_y_leyenda(lienzo, bool(puntos_ruta))
        return lienzo

    def _brujula_y_leyenda(self, lienzo: Image.Image, hay_ruta: bool):
        """Columna derecha: brújula arriba y leyenda debajo."""
        x0, y0, x1, y1 = ANCHO - 290, 340, ANCHO - 28, 590       # 262 px de ancho: caben los textos
        centro_x = (x0 + x1) // 2
        bru = self._asset("brujula.png")
        if bru is not None:
            bru = bru.resize((176, round(176 * bru.height / bru.width)), Image.LANCZOS)
            lienzo.alpha_composite(bru, (centro_x - bru.width // 2, 112))
        d = ImageDraw.Draw(lienzo)
        self._texto(d, (centro_x, 88), "N", _fuente(38), ancla="mm", contorno=4)

        panel = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
        ImageDraw.Draw(panel).rounded_rectangle((x0, y0, x1, y1), radius=18, fill=(22, 16, 12, 205),
                                                outline=ORO + (255,), width=3)
        lienzo.alpha_composite(panel)
        d = ImageDraw.Draw(lienzo)
        self._texto(d, ((x0 + x1) // 2, y0 + 30), "Leyenda", _fuente(28), ancla="mm", contorno=3)
        ficha = self._marcador(46)
        lienzo.alpha_composite(ficha, (x0 + 18, y0 + 62))
        self._texto(d, (x0 + 78, y0 + 85), "Tu posición", _fuente(22), relleno=PERGAMINO, ancla="lm", contorno=2)
        self._discontinua(d, (x0 + 18, y0 + 150), (x0 + 64, y0 + 150), CARMESI + (255,), ancho=6, trazo=12, hueco=8)
        self._texto(d, (x0 + 78, y0 + 150), "Ruta recorrida", _fuente(22), relleno=PERGAMINO, ancla="lm", contorno=2)
        self._texto(d, (x0 + 41, y0 + 212), "?", _fuente(44), ancla="mm", contorno=4)
        self._texto(d, (x0 + 78, y0 + 212), "Sin explorar", _fuente(22), relleno=PERGAMINO, ancla="lm", contorno=2)

    # ------------------------------------------------------------- API pública

    def render_fog_of_war(self, discovered_rooms: list, current_room=None, route=None, hero_name=None) -> str:
        """
        Dibuja el mapa con niebla de guerra y lo guarda en storage/maps/current_view.png.

        Args:
            discovered_rooms: IDs de las salas descubiertas.
            current_room: sala donde está el jugador. Si se omite, se toma del estado del juego.
            route: salas recorridas, en orden. Si se omite, se toma del estado del juego.
            hero_name: nombre del personaje para el título. Si se omite, se toma del estado.

        Returns:
            Ruta de la imagen generada.
        """
        try:
            from game_engine.state_manager import get_state_manager
            estado = get_state_manager().get_state()
            current_room = current_room or estado["sala_actual"]
            route = estado["ruta"] if route is None else route
            hero_name = hero_name or estado["jugador"]["nombre"]
        except Exception:
            route = route or []
            hero_name = hero_name or "Aventurero"

        descubiertas = {s for s in discovered_rooms if s in DUNGEON_LAYOUT}
        lienzo = self._componer(descubiertas, current_room, route, hero_name)
        destino = self._ruta_mapa()
        destino.parent.mkdir(parents=True, exist_ok=True)
        lienzo.convert("RGB").save(destino, compress_level=3)
        return str(destino)

    def generate_base_map(self) -> str:
        """Mapa completo, sin niebla (útil para depurar o para mostrar el dungeon entero)."""
        lienzo = self._componer(set(DUNGEON_LAYOUT), None, [], "Dungeon completo", todas_visibles=True)
        lienzo.convert("RGB").save(SALIDA_BASE, compress_level=3)
        return SALIDA_BASE

    @staticmethod
    def _ruta_mapa() -> pathlib.Path:
        """Dónde se guarda el mapa: dentro de la carpeta de la partida activa (cada una tiene el suyo)."""
        try:
            from game_engine.state_manager import get_state_manager
            return get_state_manager().ruta_mapa
        except Exception:
            return pathlib.Path(SALIDA_ACTUAL)               # respaldo si no hay gestor de partidas

    def get_current_map_path(self) -> str:
        """Ruta del mapa de la partida activa; si todavía no existe, lo dibuja."""
        if self._ruta_mapa().exists():
            return str(self._ruta_mapa())
        try:
            from game_engine.state_manager import get_state_manager
            return self.render_fog_of_war(get_state_manager().get_discovered_rooms())
        except Exception:
            return self.generate_base_map()


def get_map_renderer() -> MapRenderer:
    """Acceso al Singleton de MapRenderer."""
    return MapRenderer()
