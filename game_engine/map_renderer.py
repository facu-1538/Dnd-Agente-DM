"""
map_renderer.py - Renderizador de Mapa con Niebla de Guerra
Genera imágenes del dungeon con efectos de niebla de guerra usando Pillow.
"""
from PIL import Image, ImageDraw, ImageFont
import os
import pathlib
import json

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

MAP_WIDTH = 800
MAP_HEIGHT = 600
ROOM_SIZE = 60  # Tamaño de cada sala en píxeles (ancho y alto del rectángulo)

# Colores de los elementos del mapa (R, G, B)
CORRIDOR_COLOR = (100, 100, 100)        # Gris para los corredores
ROOM_COLOR = (150, 120, 80)             # Marrón para las salas sin descubrir en el mapa base
FOG_COLOR = (20, 20, 20)               # Negro casi puro para la niebla de guerra
DISCOVERED_COLOR = (200, 170, 120)     # Marrón claro para salas descubiertas
TEXT_COLOR = (255, 255, 255)           # Blanco para el texto de los nombres


class MapRenderer:
    """
    Renderizador del mapa del dungeon con efecto de Niebla de Guerra.
    Genera imágenes PNG del dungeon mostrando solo las salas descubiertas
    por el jugador y ocultando el resto con niebla.
    """

    # Instancia única del Singleton
    _instance = None

    def __new__(cls):
        """Implementación del patrón Singleton."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Inicializa el MapRenderer y crea los directorios necesarios."""
        if hasattr(self, '_initialized'):
            return

        # Crear directorio de mapas si no existe
        maps_dir = pathlib.Path("storage/maps")
        maps_dir.mkdir(parents=True, exist_ok=True)

        self._initialized = True

    def _get_room_rect(self, sala: dict) -> tuple:
        """
        Calcula el rectángulo (bounding box) de una sala dado su centro.

        Args:
            sala (dict): Datos de la sala con coordenadas x, y.

        Returns:
            tuple: (x0, y0, x1, y1) coordenadas del rectángulo de la sala.
        """
        half = ROOM_SIZE // 2
        x0 = sala["x"] - half
        y0 = sala["y"] - half
        x1 = sala["x"] + half
        y1 = sala["y"] + half
        return (x0, y0, x1, y1)

    def _load_font(self, size: int = 10):
        """
        Carga una fuente para el texto del mapa.
        Intenta cargar fuente del sistema; si falla, usa la fuente por defecto de Pillow.

        Args:
            size (int): Tamaño de la fuente.

        Returns:
            ImageFont: La fuente cargada.
        """
        try:
            # Intentar cargar fuente del sistema
            return ImageFont.truetype("arial.ttf", size)
        except (IOError, OSError):
            try:
                # Intentar con ruta absoluta en Windows
                return ImageFont.truetype("C:/Windows/Fonts/arial.ttf", size)
            except (IOError, OSError):
                # Usar fuente por defecto de Pillow si no hay fuentes del sistema
                return ImageFont.load_default()

    def generate_base_map(self) -> str:
        """
        Genera el mapa base del dungeon con todas las salas visibles.
        Este mapa se usa como base para aplicar la niebla de guerra.

        Returns:
            str: Ruta al archivo de imagen generado (storage/maps/base_dungeon.png).
        """
        # Crear imagen RGB negra de 800x600
        img = Image.new("RGB", (MAP_WIDTH, MAP_HEIGHT), color=(10, 10, 10))
        draw = ImageDraw.Draw(img)

        # Cargar fuente para los nombres de las salas
        font = self._load_font(size=10)

        # Paso 1: Dibujar corredores (líneas entre salas conectadas)
        # Usamos un conjunto para evitar dibujar el mismo corredor dos veces
        corredores_dibujados = set()
        for sala_id, sala in DUNGEON_LAYOUT.items():
            for conexion_id in sala["conexiones"]:
                # Crear un par ordenado para identificar el corredor de forma única
                par = tuple(sorted([sala_id, conexion_id]))
                if par not in corredores_dibujados and conexion_id in DUNGEON_LAYOUT:
                    sala_origen = DUNGEON_LAYOUT[sala_id]
                    sala_destino = DUNGEON_LAYOUT[conexion_id]
                    # Dibujar línea de corredor entre los centros de las salas
                    draw.line(
                        [(sala_origen["x"], sala_origen["y"]),
                         (sala_destino["x"], sala_destino["y"])],
                        fill=CORRIDOR_COLOR,
                        width=8
                    )
                    corredores_dibujados.add(par)

        # Paso 2: Dibujar rectángulos de salas sobre los corredores
        for sala_id, sala in DUNGEON_LAYOUT.items():
            rect = self._get_room_rect(sala)
            # Rectángulo de fondo de la sala
            draw.rectangle(rect, fill=ROOM_COLOR, outline=(200, 180, 100), width=2)

            # Nombre de la sala centrado sobre el rectángulo
            nombre = sala["nombre"]
            # Calcular posición centrada del texto
            bbox = draw.textbbox((0, 0), nombre, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            text_x = sala["x"] - text_width // 2
            text_y = sala["y"] - text_height // 2
            draw.text((text_x, text_y), nombre, fill=TEXT_COLOR, font=font)

        # Paso 3: Agregar título al mapa
        titulo_font = self._load_font(size=14)
        draw.text((10, 10), "⚔ Dungeon Map", fill=(220, 180, 60), font=titulo_font)

        # Guardar imagen del mapa base
        ruta_mapa = "storage/maps/base_dungeon.png"
        img.save(ruta_mapa)
        return ruta_mapa

    def render_fog_of_war(self, discovered_rooms: list) -> str:
        """
        Renderiza el mapa con efecto de Niebla de Guerra.
        Las salas no descubiertas se cubren con una capa oscura semitransparente.

        Args:
            discovered_rooms (list): Lista de IDs de salas descubiertas por el jugador.

        Returns:
            str: Ruta al archivo de imagen con niebla (storage/maps/current_view.png).
        """
        ruta_base = "storage/maps/base_dungeon.png"

        # Generar mapa base si no existe
        if not pathlib.Path(ruta_base).exists():
            self.generate_base_map()

        # Cargar mapa base
        img_base = Image.open(ruta_base).convert("RGBA")

        # Crear capa de niebla semitransparente negra (alpha=200 de 255)
        # Alpha 200 = niebla muy densa pero no completamente opaca
        capa_niebla = Image.new("RGBA", (MAP_WIDTH, MAP_HEIGHT), (FOG_COLOR[0], FOG_COLOR[1], FOG_COLOR[2], 200))
        draw_niebla = ImageDraw.Draw(capa_niebla)

        # Para cada sala NO descubierta, dejar la niebla
        # Para cada sala descubierta, "borrar" la niebla haciendo esa área transparente
        for sala_id, sala in DUNGEON_LAYOUT.items():
            if sala_id in discovered_rooms:
                # Sala descubierta: borrar la niebla en esta área
                rect = self._get_room_rect(sala)
                # Expandir el área revelada para incluir los corredores cercanos
                expansion = ROOM_SIZE // 2
                rect_expandido = (
                    rect[0] - expansion,
                    rect[1] - expansion,
                    rect[2] + expansion,
                    rect[3] + expansion
                )
                # Pintar con transparencia total para "quitar" la niebla
                draw_niebla.rectangle(rect_expandido, fill=(0, 0, 0, 0))

                # También revelar los corredores hacia salas también descubiertas
                for conexion_id in sala["conexiones"]:
                    if conexion_id in discovered_rooms and conexion_id in DUNGEON_LAYOUT:
                        sala_destino = DUNGEON_LAYOUT[conexion_id]
                        # Dibujar línea de corredor sin niebla
                        draw_niebla.line(
                            [(sala["x"], sala["y"]),
                             (sala_destino["x"], sala_destino["y"])],
                            fill=(0, 0, 0, 0),
                            width=12
                        )

        # Aplicar composite: mapa base + capa de niebla
        img_final = Image.alpha_composite(img_base, capa_niebla)

        # Agregar borde a las salas descubiertas para resaltarlas
        draw_final = ImageDraw.Draw(img_final)
        font = self._load_font(size=10)
        for sala_id in discovered_rooms:
            if sala_id in DUNGEON_LAYOUT:
                sala = DUNGEON_LAYOUT[sala_id]
                rect = self._get_room_rect(sala)
                # Sala descubierta con color más brillante y borde dorado
                draw_final.rectangle(rect, fill=DISCOVERED_COLOR + (255,), outline=(255, 215, 0), width=2)

                # Volver a dibujar el nombre de la sala descubierta
                nombre = sala["nombre"]
                bbox = draw_final.textbbox((0, 0), nombre, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                text_x = sala["x"] - text_width // 2
                text_y = sala["y"] - text_height // 2
                draw_final.text((text_x, text_y), nombre, fill=TEXT_COLOR, font=font)

        # Convertir a RGB para guardar como PNG sin canal alpha
        img_rgb = img_final.convert("RGB")

        # Guardar la vista actual del mapa
        ruta_actual = "storage/maps/current_view.png"
        img_rgb.save(ruta_actual)
        return ruta_actual

    def get_current_map_path(self) -> str:
        """
        Obtiene la ruta al mapa actual del jugador.
        Si existe current_view.png, lo retorna. Si no, genera el mapa base.

        Returns:
            str: Ruta al archivo de mapa más reciente disponible.
        """
        ruta_actual = "storage/maps/current_view.png"
        if pathlib.Path(ruta_actual).exists():
            return ruta_actual
        # Si no existe la vista actual, generar el mapa base
        return self.generate_base_map()


def get_map_renderer() -> MapRenderer:
    """
    Función de acceso al Singleton de MapRenderer.

    Returns:
        MapRenderer: La instancia única del renderizador de mapas.
    """
    return MapRenderer()
