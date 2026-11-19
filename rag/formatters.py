"""
formatters.py - Funciones de formateo de datos de D&D 5e como Markdown.

Extraídas de setup_rag.py para que tanto la ingesta (setup_rag) como el
fallback de la API (retriever.search_api) las compartan sin importar
el script de setup desde el retriever.
"""

import json


def format_spell(item: dict) -> str:
    """
    Formatea un hechizo de D&D 5e como texto Markdown.

    Args:
        item: Diccionario con los datos del hechizo desde la API.

    Returns:
        Texto Markdown con la información del hechizo.
    """
    nombre = item.get("name", "Sin nombre")
    nivel = item.get("level", "Desconocido")
    escuela = item.get("school", "Desconocida")
    tiempo_lanzamiento = item.get("casting_time", "Desconocido")
    rango = item.get("range", "Desconocido")
    componentes = item.get("components", "Desconocidos")
    duracion = item.get("duration", "Desconocida")
    descripcion = item.get("desc", "Sin descripción")
    descripcion_superior = item.get("higher_level", "")

    texto = f"""## {nombre}

**Nivel:** {nivel}
**Escuela:** {escuela}
**Tiempo de lanzamiento:** {tiempo_lanzamiento}
**Rango:** {rango}
**Componentes:** {componentes}
**Duración:** {duracion}

**Descripción:**
{descripcion}
"""

    if descripcion_superior:
        texto += f"\n**A niveles superiores:**\n{descripcion_superior}\n"

    return texto


def format_monster(item: dict) -> str:
    """
    Formatea un monstruo de D&D 5e como texto Markdown.

    Args:
        item: Diccionario con los datos del monstruo desde la API.

    Returns:
        Texto Markdown con la información del monstruo.
    """
    nombre = item.get("name", "Sin nombre")
    tipo = item.get("type", "Desconocido")
    cr = item.get("challenge_rating", "Desconocido")
    hp = item.get("hit_points", "Desconocido")
    dados_hp = item.get("hit_dice", "")
    ac = item.get("armor_class", "Desconocido")
    velocidad = item.get("speed", {})

    # Estadísticas de combate
    fuerza = item.get("strength", "?")
    destreza = item.get("dexterity", "?")
    constitucion = item.get("constitution", "?")
    inteligencia = item.get("intelligence", "?")
    sabiduria = item.get("wisdom", "?")
    carisma = item.get("charisma", "?")

    # Acciones del monstruo
    acciones = item.get("actions") or []
    texto_acciones = ""
    for accion in acciones:
        nombre_accion = accion.get("name", "Acción")
        desc_accion = accion.get("desc", "")
        texto_acciones += f"- **{nombre_accion}:** {desc_accion}\n"

    # Habilidades especiales
    habilidades = item.get("special_abilities") or []
    texto_habilidades = ""
    for hab in habilidades:
        nombre_hab = hab.get("name", "Habilidad")
        desc_hab = hab.get("desc", "")
        texto_habilidades += f"- **{nombre_hab}:** {desc_hab}\n"

    # Formatear velocidad
    if isinstance(velocidad, dict):
        texto_velocidad = ", ".join(f"{k}: {v}" for k, v in velocidad.items())
    else:
        texto_velocidad = str(velocidad)

    texto = f"""## {nombre}

**Tipo:** {tipo}
**Desafío (CR):** {cr}
**Puntos de Vida:** {hp} ({dados_hp})
**Clase de Armadura:** {ac}
**Velocidad:** {texto_velocidad}

**Estadísticas:**
| FUE | DES | CON | INT | SAB | CAR |
|-----|-----|-----|-----|-----|-----|
| {fuerza} | {destreza} | {constitucion} | {inteligencia} | {sabiduria} | {carisma} |
"""

    if texto_habilidades:
        texto += f"\n**Habilidades especiales:**\n{texto_habilidades}"

    if texto_acciones:
        texto += f"\n**Acciones:**\n{texto_acciones}"

    return texto


def format_simple(item: dict, fields: list) -> str:
    """
    Formatea un ítem genérico como Markdown usando los campos especificados.

    Args:
        item: Diccionario con los datos del ítem.
        fields: Lista de nombres de campos a incluir en el formato.

    Returns:
        Texto Markdown con los campos del ítem.
    """
    nombre = item.get("name", item.get("title", "Sin nombre"))
    texto = f"## {nombre}\n\n"

    for campo in fields:
        valor = item.get(campo)
        if valor:
            # Formatear el nombre del campo para mostrarlo mejor
            nombre_campo = campo.replace("_", " ").capitalize()
            if isinstance(valor, (dict, list)):
                valor = json.dumps(valor, ensure_ascii=False, indent=2)
            texto += f"**{nombre_campo}:** {valor}\n"

    return texto


def format_condition(item: dict) -> str:
    """Formatea una condición de la API v2 usando la versión del SRD 2014."""
    nombre = item.get("name", "Sin nombre")
    descripciones = item.get("descriptions") or []
    desc = next(
        (d["desc"] for d in descripciones if d.get("document") == "srd-2014"),
        None,
    )
    if not desc:
        desc = item.get("desc") or (
            descripciones[0]["desc"] if descripciones else "Sin descripción"
        )
    return f"## {nombre}\n\n**Descripción:** {desc}\n"


def format_class(item: dict) -> str:
    """Formatea una clase (o subclase) de la API v2 como Markdown, con sus rasgos ordenados por nivel."""
    nombre = item.get("name", "Sin nombre")
    base = item.get("subclass_of")
    if isinstance(base, dict):
        base = base.get("name")
    pv = item.get("hit_points") or {}
    salvaciones = ", ".join(x.get("name", "") for x in item.get("saving_throws") or [])
    principales = ", ".join(x.get("name", "") if isinstance(x, dict) else str(x)
                            for x in item.get("primary_abilities") or [])

    texto = f"## {nombre}\n\n**Tipo:** {'Subclase' if base else 'Clase'}\n"
    if base:
        texto += f"**Subclase de:** {base}\n"
    if item.get("hit_dice"):
        texto += f"**Dado de golpe:** {item['hit_dice']}\n"
    if pv.get("hit_points_at_1st_level"):
        texto += f"**Vida al nivel 1:** {pv['hit_points_at_1st_level']}\n"
    if pv.get("hit_points_at_higher_levels"):
        texto += f"**Vida en niveles superiores:** {pv['hit_points_at_higher_levels']}\n"
    if salvaciones:
        texto += f"**Salvaciones:** {salvaciones}\n"
    if principales:
        texto += f"**Habilidad principal:** {principales}\n"
    if item.get("caster_type") and str(item["caster_type"]).upper() != "NONE":
        texto += f"**Lanzador de conjuros:** {item['caster_type']}\n"
    if item.get("desc"):
        texto += f"\n{item['desc']}\n"

    def nivel_minimo(rasgo: dict) -> int:
        niveles = [g.get("level", 99) for g in rasgo.get("gained_at") or []]
        return min(niveles) if niveles else 0

    # La API trae como "rasgo" las columnas de la tabla de la clase con el texto "[Column data]": no aportan nada
    rasgos = sorted((r for r in item.get("features") or [] if "[Column data]" not in str(r.get("desc", ""))),
                    key=nivel_minimo)
    if rasgos:
        texto += "\n**Rasgos:**\n"
        for r in rasgos:
            niveles = sorted({g.get("level") for g in r.get("gained_at") or [] if g.get("level")})
            cuando = f" (nivel {', '.join(str(n) for n in niveles)})" if niveles else ""
            texto += f"- **{r.get('name', 'Rasgo')}{cuando}:** {r.get('desc', '')}\n"
    return texto
