"""
visor_mapa.py - Visor del mapa con zoom de rueda (componente de Streamlit v2, sin dependencias nuevas).

Qué resuelve: `st.image` muestra el mapa a todo el ancho del diálogo y, en una pantalla baja,
obliga a deslizar para verlo entero. Este visor:

  * Se dimensiona con unidades de pantalla (vh) y la proporción de la imagen: entra SIEMPRE completo,
    centrado y sin scroll, en cualquier pantalla (el ancho se achica si hace falta).
  * Rueda del mouse = zoom hacia el cursor (se frena el scroll de la página con preventDefault).
  * Arrastrar = mover; doble clic = acercar o volver a ajustar; botones +, - y Ajustar (útiles con
    trackpad o pantalla táctil).
  * El zoom se conserva si la app se vuelve a ejecutar (se guarda por instancia, no se reinicia).

La imagen viaja como JPEG en base64 (cacheado por archivo y fecha de modificación): el PNG
original pesa ~1.7 MB y se reenviaría en cada ejecución.
"""
import base64
import io
from pathlib import Path

import streamlit as st
from PIL import Image

# Espacio vertical que ocupa todo lo que rodea al visor dentro del diálogo (título, márgenes del
# diálogo y de la página). Se mide en el navegador; ver las pruebas en 1366x768, 1920x1080 y tablet.
MARGEN_VERTICAL = "230px"

_HTML = """
<div class="visor" role="img" aria-label="Mapa de la mazmorra">
  <img alt="" draggable="false" />
  <div class="barra">
    <button type="button" class="menos" aria-label="Alejar" title="Alejar">&minus;</button>
    <span class="zoom" aria-live="polite">100%</span>
    <button type="button" class="mas" aria-label="Acercar" title="Acercar">+</button>
    <button type="button" class="ajustar" aria-label="Ajustar a la pantalla" title="Ajustar a la pantalla">Ajustar</button>
    <a class="descargar" download="mapa_dungeon.jpg" aria-label="Descargar el mapa" title="Descargar el mapa">Descargar</a>
  </div>
  <div class="ayuda">Rueda: zoom &middot; Arrastrar: mover &middot; Doble clic: acercar</div>
</div>
"""

_CSS = """
:host { display: block; }
.visor {
  --ratio: 1.5;
  --alto-max: max(220px, calc(100vh - var(--margen, 230px)));
  position: relative;
  width: min(100%, calc(var(--alto-max) * var(--ratio)));
  aspect-ratio: var(--ratio);
  margin: 0 auto;
  overflow: hidden;
  background: #0b0b12;
  border: 1px solid var(--st-border-color, #2a2a3d);
  border-radius: 10px;
  cursor: grab;
  touch-action: none;
  user-select: none;
}
.visor.arrastrando { cursor: grabbing; }
.visor img {
  position: absolute; left: 0; top: 0;
  max-width: none; transform-origin: 0 0;
  -webkit-user-drag: none; pointer-events: none;
}
.barra {
  position: absolute; right: 10px; bottom: 10px;
  display: flex; align-items: center; gap: 6px;
  padding: 6px 8px; border-radius: 10px;
  background: rgba(15, 15, 23, 0.82); border: 1px solid var(--st-border-color, #2a2a3d);
  backdrop-filter: blur(4px);
}
.barra button, .barra a {
  min-width: 32px; height: 32px; padding: 0 10px;
  display: inline-flex; align-items: center; justify-content: center;
  font: 600 13px/1 var(--st-font, sans-serif);
  color: var(--st-text-color, #e8e0cc); background: transparent;
  border: 1px solid var(--st-border-color, #2a2a3d); border-radius: 8px;
  cursor: pointer; text-decoration: none;
}
.barra button:hover, .barra a:hover { border-color: var(--st-primary-color, #b3263a); color: #fff; }
.barra button:focus-visible, .barra a:focus-visible { outline: 2px solid var(--st-primary-color, #b3263a); outline-offset: 2px; }
.zoom { min-width: 46px; text-align: center; font: 600 13px/1 var(--st-font, sans-serif); color: var(--st-text-color, #e8e0cc); }
.ayuda {
  position: absolute; left: 12px; bottom: 12px; padding: 5px 9px; border-radius: 8px;
  font: 12px/1.2 var(--st-font, sans-serif); color: #cfc6ad; background: rgba(15, 15, 23, 0.72);
  pointer-events: none; opacity: 0.9; transition: opacity .6s;
}
.visor.activo .ayuda { opacity: 0; }
@media (max-width: 560px) { .ayuda { display: none; } .barra { right: 6px; bottom: 6px; padding: 4px 6px; } }
"""

_JS = """
const ESTADOS = new WeakMap()
const MAXIMO = 8                 // zoom máximo: 8 veces el tamaño "ajustado"

export default function (component) {
  const { data, parentElement } = component
  const raiz = parentElement.querySelector(".visor")
  const img = parentElement.querySelector("img")
  if (!raiz || !img) return

  // Estado por instancia: sobrevive a las re-ejecuciones de la app (no se pierde el zoom)
  let e = ESTADOS.get(parentElement)
  if (!e) { e = { rel: 1, x: 0, y: 0, arrastre: null }; ESTADOS.set(parentElement, e) }

  raiz.style.setProperty("--margen", data.margen || "230px")
  raiz.setAttribute("aria-label", data.alt || "Mapa")
  const etiquetaZoom = parentElement.querySelector(".zoom")
  parentElement.querySelector(".descargar").setAttribute("href", data.src)

  const ajuste = () => Math.min(raiz.clientWidth / img.naturalWidth, raiz.clientHeight / img.naturalHeight)

  function limitar() {                       // la imagen no puede salirse del cuadro
    const s = e.rel * ajuste(), w = img.naturalWidth * s, h = img.naturalHeight * s
    e.x = w <= raiz.clientWidth ? (raiz.clientWidth - w) / 2 : Math.min(0, Math.max(raiz.clientWidth - w, e.x))
    e.y = h <= raiz.clientHeight ? (raiz.clientHeight - h) / 2 : Math.min(0, Math.max(raiz.clientHeight - h, e.y))
  }
  function pintar() {
    limitar()
    img.style.transform = `translate(${e.x}px, ${e.y}px) scale(${e.rel * ajuste()})`
    etiquetaZoom.textContent = Math.round(e.rel * 100) + "%"
  }
  function zoomEn(factor, cx, cy) {          // zoom manteniendo fijo el punto (cx, cy) bajo el cursor
    const antes = e.rel
    e.rel = Math.min(MAXIMO, Math.max(1, antes * factor))
    const k = e.rel / antes
    e.x = cx - (cx - e.x) * k
    e.y = cy - (cy - e.y) * k
    raiz.classList.add("activo")
    pintar()
  }
  const centro = () => [raiz.clientWidth / 2, raiz.clientHeight / 2]

  // Rueda: zoom hacia el cursor. preventDefault evita que además se deslice el diálogo o la página.
  raiz.onwheel = (ev) => {
    ev.preventDefault()
    const r = raiz.getBoundingClientRect()
    zoomEn(Math.exp(-ev.deltaY * 0.0018), ev.clientX - r.left, ev.clientY - r.top)
  }
  // Arrastrar para mover
  raiz.onpointerdown = (ev) => {
    if (ev.target.closest(".barra")) return
    e.arrastre = { px: ev.clientX, py: ev.clientY, x0: e.x, y0: e.y }
    raiz.setPointerCapture(ev.pointerId)
    raiz.classList.add("arrastrando", "activo")
  }
  raiz.onpointermove = (ev) => {
    if (!e.arrastre) return
    e.x = e.arrastre.x0 + (ev.clientX - e.arrastre.px)
    e.y = e.arrastre.y0 + (ev.clientY - e.arrastre.py)
    pintar()
  }
  const soltar = () => { e.arrastre = null; raiz.classList.remove("arrastrando") }
  raiz.onpointerup = soltar
  raiz.onpointercancel = soltar
  // Doble clic: acerca hacia el punto, o vuelve a ajustar si ya estaba acercado
  raiz.ondblclick = (ev) => {
    if (ev.target.closest(".barra")) return
    const r = raiz.getBoundingClientRect()
    if (e.rel > 1.05) { e.rel = 1; pintar() } else { zoomEn(2.5, ev.clientX - r.left, ev.clientY - r.top) }
  }
  // Botones
  parentElement.querySelector(".mas").onclick = () => zoomEn(1.4, ...centro())
  parentElement.querySelector(".menos").onclick = () => zoomEn(1 / 1.4, ...centro())
  parentElement.querySelector(".ajustar").onclick = () => { e.rel = 1; pintar() }

  // La imagen y el tamaño del cuadro pueden cambiar (carga, rotar la pantalla, cambiar el tamaño de la ventana)
  const alCargar = () => {
    raiz.style.setProperty("--ratio", String(img.naturalWidth / img.naturalHeight))
    pintar()
  }
  img.onload = alCargar
  if (img.getAttribute("src") !== data.src) img.setAttribute("src", data.src)
  else if (img.complete && img.naturalWidth) alCargar()

  if (!e.ro) { e.ro = new ResizeObserver(() => { if (img.naturalWidth) pintar() }); e.ro.observe(raiz) }
  return () => { if (e.ro) { e.ro.disconnect(); e.ro = null } }
}
"""

# El componente se declara UNA vez, al importar el módulo (re-declararlo en cada ejecución
# registra el mismo nombre varias veces).
_VISOR = st.components.v2.component("visor_mapa", html=_HTML, css=_CSS, js=_JS)


@st.cache_data(show_spinner=False)
def _jpeg_base64(ruta: str, mtime_ns: int) -> str:
    """El mapa como JPEG (calidad 92) en base64. mtime_ns invalida la caché cuando el mapa cambia."""
    with Image.open(ruta) as im:
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=92, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def visor_mapa(ruta: Path, *, key: str, alt: str = "Mapa de la mazmorra", margen: str = MARGEN_VERTICAL):
    """
    Muestra el mapa en el visor con zoom. `margen` es el espacio vertical que ocupa lo que rodea al
    visor; el mapa se ajusta a (alto de la pantalla - margen).
    """
    ruta = Path(ruta)
    _VISOR(
        key=key,
        data={"src": _jpeg_base64(str(ruta), ruta.stat().st_mtime_ns), "alt": alt, "margen": margen},
        height="content",
    )
