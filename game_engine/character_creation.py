"""Ficha inicial validada y calculada por Python a partir de opciones SRD 2014."""
import copy
from rag.fixed_content import catalog


def default_choices(class_id: str) -> dict:
    profile = catalog()["classes"][class_id]
    book = profile["spells"][:profile["spellbook_count"]]
    return {"class_id": class_id, "name": "", "gear": next(iter(profile["gear"])),
            "skills": profile["default_skills"][:], "expertise": profile["default_skills"][:2],
            "style": "Defensa", "cantrips": profile["cantrips"][:profile["cantrip_count"]],
            "spellbook": book, "prepared": (book or profile["spells"])[:profile["prepared_count"]]}


def _selection(values, allowed, count, label):
    if not isinstance(values, list) or len(values) != count or len(set(values)) != count:
        raise ValueError(f"Elegí exactamente {count} {label}, sin repetir.")
    if not set(values).issubset(allowed):
        raise ValueError(f"Hay una opción inválida en {label}.")
    return list(values)


def build_character(choices: dict) -> dict:
    key = choices.get("class_id")
    if key not in catalog()["classes"]:
        raise ValueError("Clase inválida.")
    profile = catalog()["classes"][key]
    name = choices.get("name", "").strip()
    if not name or len(name) > 60:
        raise ValueError("Escribí un nombre de entre 1 y 60 caracteres.")
    if choices.get("gear") not in profile["gear"]:
        raise ValueError("Elegí un equipo válido.")
    gear = profile["gear"][choices["gear"]]
    skills = _selection(choices.get("skills"), profile["skills"], profile["skill_count"], "habilidades")
    abilities = copy.deepcopy(profile["abilities"])
    modifiers = {a: (v - 10) // 2 for a, v in abilities.items()}
    expertise = _selection(choices.get("expertise"), skills, 2, "pericias") if key == "rogue" else []
    cantrips = _selection(choices.get("cantrips", []), profile["cantrips"], profile["cantrip_count"], "trucos")
    book = _selection(choices.get("spellbook", []), profile["spells"], profile["spellbook_count"], "hechizos del libro")
    prepared = _selection(choices.get("prepared", []), book if key == "wizard" else profile["spells"],
                          profile["prepared_count"], "hechizos preparados")
    style = choices.get("style", "Defensa") if key == "fighter" else ""
    if key == "fighter" and (style not in profile["styles"] or (style == "Duelo" and choices["gear"] != "guardian")):
        raise ValueError("Duelo requiere la espada a una mano con escudo; para dos manos elegí Defensa.")
    ac = gear["ac"] + gear.get("shield", 0)
    if gear.get("add_dex"):
        ac += min(modifiers["destreza"], gear.get("dex_cap", modifiers["destreza"]))
    ac += int(style == "Defensa")
    attacks = [{"nombre": a["nombre"], "bonificador": modifiers[a["atributo"]] + 2,
                "danio": f"{a['dado']}{modifiers[a['atributo']] + (2 if style == 'Duelo' else 0):+d}",
                "tipo": a["tipo"]} for a in gear["attacks"]]
    spells = {"trucos": cantrips, "libro": book, "preparados": prepared,
              "dominio": profile.get("domain_spells", [])}
    if profile.get("casting"):
        spells.update({"atributo": profile["casting"], "cd": 10 + modifiers[profile["casting"]],
                       "ataque": 2 + modifiers[profile["casting"]], "espacios": {"1": {"max": 2, "restantes": 2}}})
    return {"nombre": name, "raza": "", "clase": profile["label"], "class_id": key,
            "nivel": 1, "ficha_creada": True, "reglamento": "srd-2014", "creacion": "guiada-v1",
            "hp": 100, "max_hp": 100, "oro": 50, "experiencia": 0,
            "atributos": abilities, "modificadores": modifiers, "competencia": 2, "ca": ac,
            "habilidades": {s: modifiers[catalog()["skills"][s]] + (4 if s in expertise else 2) for s in skills},
            "pericias": expertise, "salvaciones": {a: modifiers[a] + (2 if a in profile["saves"] else 0) for a in abilities},
            "equipo": list(gear["items"]), "ataques": attacks, "estilo": style,
            "rasgos": list(profile["features"]), "subclase": profile.get("subclass", ""), "magia": spells}
