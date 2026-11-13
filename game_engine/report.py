"""
report.py - Crónica de la aventura en PDF (ReportLab).

Arma el informe final de la partida: portada, ficha del héroe, resumen, mapa con la ruta
recorrida, cronología de sucesos, balance de combate y reglas consultadas con sus fuentes.

Todos los datos salen del ESTADO del juego (ficha, ruta, eventos); el `summary` que escribe
el modelo es solo un epílogo narrativo y tiene respaldo si llega vacío: tras recargar la
página el modelo ya no recuerda la partida, pero el estado sí.

Solo usa fuentes estándar de PDF (Times y Helvetica), que no traen todos los caracteres
Unicode: por eso el texto pasa por `_seguro()` antes de dibujarse.
"""
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, Image as RLImage, KeepTogether,
    NextPageTemplate, PageBreak, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

ASSETS = Path("assets/map")
# Carpeta de salida. None = la carpeta "informes" de la partida activa (lo normal); se puede fijar
# a mano, por ejemplo en las pruebas.
REPORTS_DIR = None

# Paleta (la misma de la interfaz y del mapa)
TINTA = colors.HexColor("#2A2320")
CARMESI = colors.HexColor("#8E1B2D")
ORO = colors.HexColor("#A67C00")
ORO_CLARO = colors.HexColor("#E8C468")
SEPIA = colors.HexColor("#8B5E34")
PERGAMINO = colors.HexColor("#EAD7A6")
PERGAMINO_2 = colors.HexColor("#F2E4BF")

# Tipo de evento -> (etiqueta, color)
TIPOS = {
    "dados": ("Dados", SEPIA),
    "danio": ("Daño", CARMESI),
    "curacion": ("Curación", colors.HexColor("#2E6B3A")),
    "enemigo": ("Enemigo", colors.HexColor("#6B1220")),
    "mapa": ("Exploración", colors.HexColor("#1F5C66")),
    "personaje": ("Personaje", ORO),
    "reglas": ("Reglamento", colors.HexColor("#2F4C8C")),
    "nivel": ("Nivel", ORO),
}

MAX_EVENTOS_EN_PDF = 150      # los más recientes; el resto sigue en el estado

# Tope del registro de eventos en el estado (el balance se calcula sobre lo que queda)
from game_engine.state_manager import MAX_EVENTOS as MAX_REGISTRO


# ---------------------------------------------------------------------------
# Limpieza de texto
# ---------------------------------------------------------------------------

_REEMPLAZOS = {"→": "a", "←": "de", "–": "-", "…": "...", " ": " ", "’": "'", "‘": "'"}


def _seguro(texto) -> str:
    """
    Texto apto para las fuentes estándar de PDF (cp1252) y para el marcado de `Paragraph`.
    Quita el markdown, reemplaza lo que no existe en la fuente (flechas, emojis) y escapa
    `<`, `>` y `&`, que `Paragraph` interpretaría como etiquetas.
    """
    texto = "" if texto is None else str(texto)                # ojo: 0 es un valor válido, no vacío
    texto = re.sub(r"[*_`#]+", "", texto)                      # markdown
    salida = []
    for c in texto:
        c = _REEMPLAZOS.get(c, c)
        try:
            c.encode("cp1252")
            salida.append(c)
        except UnicodeEncodeError:
            pass                                                # emojis, símbolos: se omiten
    return escape("".join(salida)).strip()


def _slug(texto: str) -> str:
    """Nombre de archivo seguro a partir de un nombre elegido por el jugador."""
    ascii_ = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")[:40] or "aventura"


def _texto_evento(e: dict) -> str:
    """Redacta un evento del registro para el PDF (sin flechas, que la fuente no trae)."""
    t = e["texto"]
    if e["tipo"] in ("danio", "curacion"):
        t = re.sub(r"(\d+) → (\d+) HP", r"de \1 a \2 HP", t)
    elif e["tipo"] == "dados":
        t = t.replace(" → ", " = ")
    elif e["tipo"] == "reglas":
        t = t.replace(" → ", ". Fuentes: ")
    return _seguro(t)


# ---------------------------------------------------------------------------
# Datos derivados del estado
# ---------------------------------------------------------------------------

def _resumen_automatico(estado: dict, nombres_ruta: list, derrotados: int, xp: int, oro: int) -> str:
    """Resumen de respaldo, armado solo con datos del estado."""
    j = estado["jugador"]
    salas = len(estado["salas_descubiertas"])
    partes = [
        f"{j['nombre']}, {j['raza'] or 'aventurero'} {j['clase']} de nivel {j['nivel']}, "
        f"exploró {salas} sala{'s' if salas != 1 else ''} del dungeon"
    ]
    if len(nombres_ruta) > 1:
        partes[0] += f" y recorrió {len(nombres_ruta)} tramos hasta {nombres_ruta[-1]}"
    partes[0] += "."
    if derrotados:
        partes.append(f"Derrotó a {derrotados} enemigo{'s' if derrotados != 1 else ''} "
                      f"y ganó {xp} XP y {oro} monedas de oro.")
    partes.append(f"Termina la jornada con {j['hp']} de {j['max_hp']} puntos de vida.")
    return " ".join(partes)


def _balance(eventos: list) -> dict:
    """Cifras del combate a partir del registro de eventos."""
    b = {"tiradas": 0, "exitos": 0, "fallos": 0, "derrotados": 0, "xp": 0, "oro": 0, "danio": 0}
    for e in eventos:
        t = e["texto"]
        if e["tipo"] == "dados":
            b["tiradas"] += 1
            b["exitos"] += "(éxito)" in t
            b["fallos"] += "(fallo)" in t
        elif e["tipo"] == "enemigo" and "derrotado" in t:
            b["derrotados"] += 1
            m = re.search(r"\+(\d+) XP, \+(\d+) oro", t)
            if m:
                b["xp"] += int(m.group(1))
                b["oro"] += int(m.group(2))
        elif e["tipo"] == "danio":
            m = re.search(r"recibe (\d+) de daño", t)
            if m:
                b["danio"] += int(m.group(1))
    return b


def _fuentes_consultadas(eventos: list) -> list:
    """Documentos del reglamento consultados (sin repetir, en orden)."""
    vistas = []
    for e in eventos:
        if e["tipo"] == "reglas" and " → " in e["texto"]:
            for f in e["texto"].split(" → ", 1)[1].split(";"):
                f = f.strip()
                if f and f not in vistas:
                    vistas.append(f)
    return vistas


# ---------------------------------------------------------------------------
# Páginas (fondos)
# ---------------------------------------------------------------------------

def _cubrir(c, img: ImageReader, ancho: float, alto: float):
    """Dibuja una imagen ocupando toda la página (recortando lo que sobre, como 'cover' en CSS)."""
    iw, ih = img.getSize()
    escala = max(ancho / iw, alto / ih)
    w, h = iw * escala, ih * escala
    c.drawImage(img, (ancho - w) / 2, (alto - h) / 2, w, h)


def _degradado(c, x, y, w, h, hacia_arriba: bool, alfa_max=0.82, pasos=28):
    """Banda negra que se desvanece, para que el texto dorado se lea sobre la ilustración."""
    c.saveState()
    c.setFillColor(colors.black)
    for i in range(pasos):
        c.setFillAlpha(alfa_max * (1 - i / pasos) ** 1.4)
        paso = h / pasos
        yi = y + i * paso if hacia_arriba else y + h - (i + 1) * paso
        c.rect(x, yi, w, paso + 1, stroke=0, fill=1)
    c.restoreState()


def _hacer_portada(estado: dict, portada: ImageReader):
    j = estado["jugador"]
    ancho, alto = A4

    def dibujar(c, doc):
        c.saveState()
        _cubrir(c, portada, ancho, alto)
        _degradado(c, 0, alto - 230, ancho, 230, hacia_arriba=False)     # arriba: título
        _degradado(c, 0, 0, ancho, 260, hacia_arriba=True)               # abajo: héroe

        c.setFillColor(ORO_CLARO)
        c.setFont("Times-Bold", 44)
        c.drawCentredString(ancho / 2, alto - 92, "CRÓNICA")
        c.setFont("Times-Roman", 21)
        c.drawCentredString(ancho / 2, alto - 124, "DE LA AVENTURA")
        c.setStrokeColor(ORO_CLARO)
        c.setLineWidth(1.2)
        c.line(ancho / 2 - 90, alto - 142, ancho / 2 + 90, alto - 142)

        c.setFillColor(colors.white)
        c.setFont("Times-Bold", 30)
        c.drawCentredString(ancho / 2, 128, _seguro_canvas(j["nombre"]))
        c.setFillColor(PERGAMINO)
        c.setFont("Times-Italic", 16)
        ficha = " ".join(p for p in (j["raza"], j["clase"]) if p) or "Aventurero"
        c.drawCentredString(ancho / 2, 100, _seguro_canvas(f"{ficha}  ·  Nivel {j['nivel']}"))
        c.setFont("Helvetica", 9)
        c.setFillColor(ORO_CLARO)
        c.drawCentredString(ancho / 2, 52, datetime.now().strftime("%d/%m/%Y  ·  Dungeon Master AI"))
        c.restoreState()

    return dibujar


def _seguro_canvas(texto) -> str:
    """Como _seguro, pero para canvas (que no interpreta marcado): sin escapar entidades."""
    s = _seguro(texto)
    return s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def _hacer_fondo(estado: dict, pergamino: ImageReader):
    nombre = _seguro_canvas(estado["jugador"]["nombre"])
    ancho, alto = A4

    def dibujar(c, doc):
        c.saveState()
        _cubrir(c, pergamino, ancho, alto)
        # filete dorado superior e inferior
        c.setStrokeColor(ORO)
        c.setLineWidth(0.8)
        c.line(54, alto - 40, ancho - 54, alto - 40)
        c.line(54, 44, ancho - 54, 44)
        c.setFillColor(SEPIA)
        c.setFont("Times-Italic", 9)
        c.drawString(54, alto - 34, "Dungeon Master AI")
        c.drawRightString(ancho - 54, alto - 34, f"Crónica de {nombre}")
        c.setFont("Times-Roman", 10)
        c.drawCentredString(ancho / 2, 30, f"Página {doc.page - 1}")      # la portada es la página 1
        c.restoreState()

    return dibujar


# ---------------------------------------------------------------------------
# Construcción del PDF
# ---------------------------------------------------------------------------

def _estilos() -> dict:
    return {
        "h1": ParagraphStyle("h1", fontName="Times-Bold", fontSize=19, leading=23, textColor=CARMESI,
                             spaceBefore=14, spaceAfter=2, keepWithNext=1),
        "cuerpo": ParagraphStyle("cuerpo", fontName="Times-Roman", fontSize=11.5, leading=16.5,
                                 textColor=TINTA, spaceAfter=6),
        "cursiva": ParagraphStyle("cursiva", fontName="Times-Italic", fontSize=12, leading=17.5,
                                  textColor=TINTA, spaceAfter=6, leftIndent=8, rightIndent=8),
        "pie": ParagraphStyle("pie", fontName="Times-Italic", fontSize=9.5, leading=12, textColor=SEPIA,
                              alignment=1, spaceBefore=3),
        "celda": ParagraphStyle("celda", fontName="Times-Roman", fontSize=10, leading=12.5, textColor=TINTA),
        "etiqueta": ParagraphStyle("etiqueta", fontName="Helvetica-Bold", fontSize=8.5, leading=10,
                                   textColor=CARMESI),
        "cab": ParagraphStyle("cab", fontName="Helvetica-Bold", fontSize=8.5, leading=10,
                              textColor=colors.white),
        "num": ParagraphStyle("num", fontName="Helvetica", fontSize=8.5, leading=10.5, textColor=SEPIA),
    }


def _titulo(texto: str, e: dict) -> list:
    """Título de sección con filete dorado. Ambos quedan pegados a lo que sigue (keepWithNext):
    así un título nunca queda solo al pie de una página."""
    filete = HRFlowable(width="100%", thickness=1, color=ORO, spaceAfter=7)
    filete.keepWithNext = True
    return [Paragraph(_seguro(texto), e["h1"]), filete]


def build_report(summary: str = "", ruta_mapa: str = "") -> dict:
    """
    Genera el PDF de la crónica y devuelve {"pdf", "paginas", "eventos", "kb"}.

    Args:
        summary: epílogo narrativo escrito por el modelo (opcional; con respaldo automático).
        ruta_mapa: imagen del mapa a incrustar (si falta, se omite esa sección).
    """
    # Importes locales: evitan ciclos y mantienen este módulo usable sin la UI
    from game_engine.state_manager import get_state_manager
    from game_engine.map_renderer import DUNGEON_LAYOUT

    estado = get_state_manager().get_state()
    j = estado["jugador"]
    eventos = estado["eventos"]
    e = _estilos()
    ancho_util = A4[0] - 2 * 54

    # Fondos: portada, y el pergamino girado a vertical para las páginas interiores
    carpeta = Path(REPORTS_DIR) if REPORTS_DIR else get_state_manager().dir_informes
    carpeta.mkdir(parents=True, exist_ok=True)
    portada = ImageReader(str(ASSETS / "portada.jpg"))
    # El pergamino se gira a vertical y se guarda en un archivo: ReportLab incrusta UNA sola vez
    # las imágenes que se dibujan por ruta (con un objeto PIL se duplicaba en cada página).
    ruta_perg = carpeta / ".pergamino_vertical.jpg"
    Image.open(ASSETS / "fondo_pergamino.jpg").rotate(90, expand=True).save(ruta_perg, quality=85)
    pergamino = ImageReader(str(ruta_perg))
    pdf_path = carpeta / f"cronica_{_slug(j['nombre'])}_{datetime.now():%Y%m%d_%H%M%S}.pdf"

    doc = BaseDocTemplate(
        str(pdf_path), pagesize=A4, leftMargin=54, rightMargin=54, topMargin=62, bottomMargin=62,
        title=f"Crónica de {_seguro_canvas(j['nombre'])}", author="Dungeon Master AI",
        subject="Crónica de la aventura", creator="Dungeon Master AI",
    )
    marco_portada = Frame(0, 0, 1, 1, id="portada", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    marco = Frame(doc.leftMargin, doc.bottomMargin, ancho_util, A4[1] - doc.topMargin - doc.bottomMargin, id="texto")
    doc.addPageTemplates([
        PageTemplate(id="portada", frames=[marco_portada], onPage=_hacer_portada(estado, portada)),
        PageTemplate(id="contenido", frames=[marco], onPage=_hacer_fondo(estado, pergamino)),
    ])

    nombres_ruta = [DUNGEON_LAYOUT.get(s, {}).get("nombre", s) for s in estado["ruta"]]
    b = _balance(eventos)
    story = [Spacer(1, 1), NextPageTemplate("contenido"), PageBreak()]

    # --- El héroe --------------------------------------------------------
    story += _titulo("El héroe", e)
    celdas = [
        ("Nombre", j["nombre"]), ("Raza", j["raza"] or "—"),
        ("Clase", j["clase"]), ("Nivel", j["nivel"]),
        ("Vida final", f"{j['hp']} / {j['max_hp']}"), ("Oro", j["oro"]),
        ("Experiencia", f"{j['experiencia']} XP"), ("Salas exploradas",
                                                    f"{len(estado['salas_descubiertas'])} de {len(DUNGEON_LAYOUT)}"),
    ]
    filas = []
    for i in range(0, len(celdas), 2):
        fila = []
        for etiqueta, valor in celdas[i:i + 2]:
            fila += [Paragraph(_seguro(etiqueta).upper(), e["etiqueta"]), Paragraph(_seguro(valor), e["celda"])]
        filas.append(fila)
    ficha = Table(filas, colWidths=[78, ancho_util / 2 - 78] * 2)
    ficha.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PERGAMINO_2),
        ("BOX", (0, 0), (-1, -1), 0.8, SEPIA), ("INNERGRID", (0, 0), (-1, -1), 0.3, SEPIA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [ficha]

    # --- Resumen ---------------------------------------------------------
    story += _titulo("Resumen de la aventura", e)
    texto_resumen = _seguro(summary)[:2200] or _seguro(
        _resumen_automatico(estado, nombres_ruta, b["derrotados"], b["xp"], b["oro"]))
    for parrafo in [p for p in re.split(r"\n\s*\n", texto_resumen) if p.strip()] or [texto_resumen]:
        story.append(Paragraph(parrafo.replace("\n", " "), e["cursiva"]))

    # --- Mapa ------------------------------------------------------------
    if ruta_mapa and Path(ruta_mapa).exists():
        # El mapa se incrusta como JPEG: el PNG sin pérdida pesaba ~3 MB dentro del PDF
        mapa_img = Image.open(ruta_mapa).convert("RGB")
        ruta_jpg = carpeta / ".mapa_para_pdf.jpg"
        mapa_img.save(ruta_jpg, quality=88, optimize=True)
        alto_img = ancho_util * mapa_img.height / mapa_img.width
        pie = "Ruta recorrida: " + " › ".join(_seguro(n) for n in nombres_ruta) if nombres_ruta else ""
        # Título + imagen + pie en un solo bloque: si no entran, pasan JUNTOS a la página siguiente
        story.append(KeepTogether(_titulo("Mapa del dungeon", e) + [
            RLImage(str(ruta_jpg), width=ancho_util, height=alto_img), Paragraph(pie, e["pie"])]))

    # --- Balance ---------------------------------------------------------
    story += _titulo("Balance de la aventura", e)
    balance = [
        ("Tiradas de dados", f"{b['tiradas']}  ({b['exitos']} éxitos, {b['fallos']} fallos)"),
        ("Enemigos derrotados", b["derrotados"]), ("XP ganada en combate", b["xp"]),
        ("Oro obtenido", b["oro"]), ("Daño recibido", f"{b['danio']} puntos de vida"),
    ]
    tabla_b = Table([[Paragraph(_seguro(k).upper(), e["etiqueta"]), Paragraph(_seguro(v), e["celda"])]
                     for k, v in balance], colWidths=[150, ancho_util - 150])
    tabla_b.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PERGAMINO_2), ("BOX", (0, 0), (-1, -1), 0.8, SEPIA),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, SEPIA), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tabla_b)
    if len(eventos) >= MAX_REGISTRO:
        story.append(Paragraph(f"El balance se calcula sobre los últimos {MAX_REGISTRO} sucesos registrados.", e["pie"]))

    # --- Cronología ------------------------------------------------------
    story += _titulo("Cronología", e)
    recientes = eventos[-MAX_EVENTOS_EN_PDF:]
    if len(eventos) > len(recientes):
        story.append(Paragraph(f"Se muestran los últimos {len(recientes)} de {len(eventos)} sucesos.", e["pie"]))
    if recientes:
        filas = [[Paragraph("N.º", e["cab"]), Paragraph("TIPO", e["cab"]), Paragraph("SUCESO", e["cab"])]]
        offset = len(eventos) - len(recientes)
        for n, ev in enumerate(recientes, offset + 1):
            etiqueta, color = TIPOS.get(ev["tipo"], (ev["tipo"].capitalize(), SEPIA))
            filas.append([
                Paragraph(str(n), e["num"]),
                Paragraph(f'<font color="{color.hexval().replace("0x", "#")}"><b>{_seguro(etiqueta)}</b></font>', e["celda"]),
                Paragraph(_texto_evento(ev), e["celda"]),
            ])
        tabla = Table(filas, colWidths=[32, 72, ancho_util - 104], repeatRows=1)
        estilo = [
            ("BACKGROUND", (0, 0), (-1, 0), CARMESI), ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOX", (0, 0), (-1, -1), 0.8, SEPIA), ("LINEBELOW", (0, 1), (-1, -1), 0.25, SEPIA),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]
        for r in range(1, len(filas)):
            estilo.append(("BACKGROUND", (0, r), (-1, r), PERGAMINO_2 if r % 2 else PERGAMINO))
        tabla.setStyle(TableStyle(estilo))
        story.append(tabla)
    else:
        story.append(Paragraph("Todavía no se registró ningún suceso.", e["cuerpo"]))

    # --- Reglas consultadas ---------------------------------------------
    fuentes = _fuentes_consultadas(eventos)
    if fuentes:
        story += _titulo("Reglas consultadas", e)
        story.append(Paragraph("Fragmentos del reglamento (SRD 5.1) que el Dungeon Master usó, con su documento de origen:",
                               e["cuerpo"]))
        for f in fuentes:
            story.append(Paragraph(f"•&nbsp;&nbsp;{_seguro(f)}", e["cuerpo"]))

    doc.build(story)
    return {"pdf": str(pdf_path), "paginas": doc.page, "eventos": len(eventos),
            "kb": round(pdf_path.stat().st_size / 1024)}
