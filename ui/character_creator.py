"""Creador visual de personaje: elegir, preparar y confirmar sin llamadas al LLM."""
import html
import streamlit as st

from game_engine.character_creation import default_choices, build_character
from rag.fixed_content import profiles, starting_documents, catalog

SPELL_NAMES = {
    "Fire Bolt": "Descarga de fuego", "Light": "Luz", "Mage Hand": "Mano de mago",
    "Minor Illusion": "Ilusión menor", "Prestidigitation": "Prestidigitación", "Ray of Frost": "Rayo de escarcha",
    "Burning Hands": "Manos ardientes", "Charm Person": "Hechizar persona", "Detect Magic": "Detectar magia",
    "Mage Armor": "Armadura de mago", "Magic Missile": "Proyectil mágico", "Shield": "Escudo",
    "Sleep": "Dormir", "Thunderwave": "Onda atronadora", "Guidance": "Guía", "Resistance": "Resistencia",
    "Sacred Flame": "Llama sagrada", "Spare the Dying": "Estabilizar", "Thaumaturgy": "Taumaturgia",
    "Command": "Orden imperiosa", "Guiding Bolt": "Proyectil guiado", "Healing Word": "Palabra de curación",
    "Sanctuary": "Santuario", "Shield of Faith": "Escudo de fe", "Bless": "Bendecir", "Cure Wounds": "Curar heridas",
}
ABILITY_NAMES = {"fuerza": "FUE", "destreza": "DES", "constitucion": "CON",
                 "inteligencia": "INT", "sabiduria": "SAB", "carisma": "CAR"}


def _spell(name):
    return SPELL_NAMES.get(name, name)


def _choose(prefix, selected):
    st.session_state[prefix + "class"] = selected
    st.session_state[prefix + "draft"] = default_choices(selected)
    for key in list(st.session_state):
        if key.startswith(prefix + selected + "_"):
            del st.session_state[key]


def _step(prefix, step):
    st.session_state[prefix + "step"] = step


def _safe_multiselect(label, options, default, count, key, **kwargs):
    # Al cambiar el libro/habilidades, se eliminan elecciones dependientes que ya no son válidas.
    valid = [v for v in st.session_state.get(key, default) if v in options][:count]
    if key not in st.session_state or valid != st.session_state[key]:
        st.session_state[key] = valid
    return st.multiselect(label, options, default=None,
                          max_selections=count, key=key, **kwargs)


def _stats(character):
    values = [("VITALIDAD", f"{character['hp']} PV"), ("ARMADURA", f"CA {character['ca']}"),
              ("COMPETENCIA", f"+{character['competencia']}")]
    st.html('<div class="hero-stats">' + ''.join(
        f'<div><small>{label}</small><strong>{value}</strong></div>' for label, value in values) + '</div>')
    st.html('<div class="ability-grid">' + ''.join(
        f'<div><small>{ABILITY_NAMES[a]}</small><b>{value}</b><span>{character["modificadores"][a]:+d}</span></div>'
        for a, value in character["atributos"].items()) + '</div>')


def _sources(class_key):
    with st.expander("Reglas y fuentes de tu clase", icon=":material/menu_book:"):
        profile = profiles()[class_key]
        st.caption("SRD 5.1 · Reglas de 2014. Esta aventura adapta la creación sin raza ni trasfondo y usa 100 PV.")
        for feature in profile["features"]:
            st.markdown(f"- {feature}")
        st.link_button("Consultar el reglamento original", catalog()["source"])
        with st.expander("Ver el texto completo de la clase"):
            for doc in starting_documents("crear_personaje", class_key):
                if doc.metadata.get("categoria") == "classes":
                    st.markdown(doc.page_content)


def render_creator(partida_id: str) -> dict | None:
    prefix = f"create_{partida_id}_"
    cards = profiles()
    step = st.session_state.setdefault(prefix + "step", 1)
    selected = st.session_state.get(prefix + "class", "")
    if st.session_state.get(prefix + "visible_step") != step:
        st.session_state[prefix + "visible_step"] = step
        st.html(f"""<script>/* creator step {step} */
        (() => {{
          let tries = 0;
          const timer = setInterval(() => {{
            const main = document.querySelector('[data-testid="stMain"]');
            if (main) main.scrollTo({{top: 0, behavior: 'instant'}});
            if (++tries >= 6) clearInterval(timer);
          }}, 100);
        }})();</script>""", unsafe_allow_javascript=True)
    if step == 1:
        st.html('<div class="creator-hero"><span class="creator-eyebrow">EL UMBRAL DE TU AVENTURA</span>'
                '<h2>Todo héroe tiene un comienzo.</h2><p>La mazmorra puede esperar un instante. '
                'Primero, elegí quién va a cruzar sus puertas.</p></div>')
    else:
        title = "Dale forma a tu héroe." if step == 2 else "Tu aventura ya tiene nombre."
        st.html(f'<div class="creator-hero compact"><span class="creator-eyebrow">EL UMBRAL DE TU AVENTURA</span>'
                f'<h2>{title}</h2></div>')
    st.html('<div class="creator-steps">' + ''.join(
        f'<div class="{"active" if i == step else "done" if i < step else ""}"><b>{i:02}</b><span>{label}</span></div>'
        for i, label in enumerate(("Elegí tu clase", "Dale forma", "Revisá tu ficha"), 1)) + '</div>')

    if step == 1:
        for row in (list(cards)[:2], list(cards)[2:]):
            columns = st.columns(2, gap="medium")
            for column, key in zip(columns, row):
                p = cards[key]
                with column, st.container(key=f"class_card_{key}"):
                    chosen = key == selected
                    st.html(f'<div class="class-card {"chosen" if chosen else ""}" style="--class-color:{p["color"]}">'
                            f'<div class="class-art"><span class="class-glyph">{p["icon"]}</span>'
                            f'<span class="class-orbit"></span><span class="class-number">0{list(cards).index(key)+1}</span></div>'
                            f'<small>{p["tag"]}</small><h3>{p["label"]}</h3><p>{p["description"]}</p>'
                            f'<div class="class-tags"><span>{p["role"]}</span><span>{p["difficulty"]}</span></div></div>')
                    st.button(f"{p['label']} elegido" if chosen else f"Elegir {p['label'].lower()}",
                              key=prefix + "choose_" + key, width="stretch", type="primary" if chosen else "secondary",
                              on_click=_choose, args=(prefix, key))
        st.caption("Todos empiezan en nivel 1 con 100 PV. Cada clase abre una forma distinta de vivir la historia.")
        with st.expander("Comparar las cuatro clases", icon=":material/compare_arrows:"):
            st.table([{"Clase": p["label"], "Estilo": p["role"], "Atributos clave": p["focus"]} for p in cards.values()])
            for doc in starting_documents("comparar_clases"):
                if doc.metadata.get("categoria") == "inicio":
                    st.markdown(doc.page_content)
        st.button("Darle forma a mi héroe", icon=":material/arrow_forward:", type="primary", width="stretch",
                  disabled=not selected, on_click=_step, args=(prefix, 2), key=prefix + "next")
        return None

    if selected not in cards:
        _step(prefix, 1)
        st.rerun()
    p = cards[selected]
    draft = st.session_state[prefix + "draft"]
    if step == 2:
        st.html(f'<div class="chosen-banner" style="--class-color:{p["color"]}">'
                f'<span class="class-glyph">{p["icon"]}</span><div><small>TU CAMINO</small>'
                f'<strong>{p["label"]}</strong><span>{p["role"]} · Nivel 1</span></div></div>')
        field = prefix + selected + "_"
        draft["name"] = st.text_input("¿Cómo se llama tu héroe?", value=draft["name"], max_chars=60,
                                      placeholder="Un nombre que merezca una historia", key=field + "name")
        st.markdown("**Tu equipo inicial**")
        draft["gear"] = st.radio("Elegí tu equipo", list(p["gear"]),
                                 format_func=lambda k: p["gear"][k]["label"], horizontal=True,
                                 key=field + "gear", label_visibility="collapsed")
        st.caption(p["gear"][draft["gear"]]["description"])
        if selected == "fighter":
            styles = ["Defensa", "Duelo"] if draft["gear"] == "guardian" else ["Defensa"]
            if field + "style" in st.session_state and st.session_state[field + "style"] not in styles:
                del st.session_state[field + "style"]
            draft["style"] = st.radio("Estilo de combate", styles, horizontal=True, key=field + "style")
            st.caption("Defensa: +1 CA con armadura. Duelo: +2 al daño con la espada a una mano y escudo.")
        st.markdown("**Lo que sabés hacer**")
        draft["skills"] = _safe_multiselect(f"Elegí {p['skill_count']} habilidades", p["skills"], draft["skills"],
                                             p["skill_count"], field + "skills")
        if selected == "rogue":
            draft["expertise"] = _safe_multiselect("Elegí 2 habilidades con pericia", draft["skills"],
                                                    draft["expertise"], 2, field + "expertise")
            st.caption("La pericia duplica tu bonificador de competencia en esas habilidades.")
        if p["cantrip_count"]:
            st.markdown("**Tu primera magia**")
            draft["cantrips"] = _safe_multiselect("Elegí 3 trucos", p["cantrips"], draft["cantrips"], 3,
                                                  field + "cantrips", format_func=_spell)
            st.caption("Los trucos se pueden lanzar sin gastar espacios de hechizo.")
            if selected == "wizard":
                draft["spellbook"] = _safe_multiselect("Elegí los 6 hechizos de tu libro", p["spells"],
                                                       draft["spellbook"], 6, field + "book", format_func=_spell)
            available = draft["spellbook"] if selected == "wizard" else p["spells"]
            draft["prepared"] = _safe_multiselect("Prepará 3 hechizos para la aventura", available,
                                                  draft["prepared"], 3, field + "prepared", format_func=_spell)
            st.caption("Empezás con 2 espacios de nivel 1. Preparar un hechizo no consume un espacio.")
            if selected == "cleric":
                st.info("Dominio de la Vida: Bendecir y Curar heridas siempre están preparados, además de tus tres elecciones.")
            with st.expander("Consultar las descripciones de magia", icon=":material/auto_fix_high:"):
                for doc in starting_documents("magia_inicial", selected):
                    if doc.metadata.get("categoria") == "inicio":
                        st.markdown(doc.page_content)
                for title in draft["cantrips"] + draft["prepared"] + p.get("domain_spells", []):
                    st.markdown(f"**{_spell(title)}**")
                    docs = [d for d in starting_documents("magia_inicial", selected) if d.metadata.get("titulo") == title]
                    for doc in docs:
                        st.markdown(doc.page_content)
        valid = True
        try:
            character = build_character(draft)
            _stats(character)
        except ValueError as e:
            valid = False
            st.caption(str(e))
        _sources(selected)
        back, forward = st.columns([1, 2])
        back.button("Volver a las clases", key=prefix + "back1", on_click=_step, args=(prefix, 1), width="stretch")
        forward.button("Revisar mi ficha", icon=":material/arrow_forward:", key=prefix + "review", type="primary",
                       disabled=not valid, on_click=_step, args=(prefix, 3), width="stretch")
        return None

    character = build_character(draft)
    st.html(f'<div class="review-name"><small>ESTA ES TU HISTORIA</small><h2>{html.escape(character["nombre"])}</h2>'
            f'<p>{p["label"]} · Nivel 1 · {html.escape(p.get("subclass", p["role"]))}</p></div>')
    _stats(character)
    left, right = st.columns(2)
    with left:
        st.markdown("**Ataques y habilidades**")
        for a in character["ataques"]:
            st.markdown(f"{a['nombre']} · ataque **{a['bonificador']:+d}** · daño **{a['danio']}**")
        st.caption(" · ".join(f"{s} {v:+d}" for s, v in character["habilidades"].items()))
    with right:
        st.markdown("**En tu mochila**")
        st.caption(" · ".join(character["equipo"]))
        st.caption("50 monedas de oro al comenzar.")
    if p.get("casting"):
        magic = character["magia"]
        st.markdown(f"**Magia** · CD **{magic['cd']}** · ataque **{magic['ataque']:+d}** · **2** espacios de nivel 1")
        st.caption("Trucos: " + ", ".join(_spell(s) for s in magic["trucos"]))
        st.caption("Preparados: " + ", ".join(_spell(s) for s in magic["preparados"] + magic["dominio"]))
    _sources(selected)
    st.caption("Al confirmar se guarda tu ficha y comienza la aventura en la entrada de la mazmorra.")
    back, confirm = st.columns([1, 2])
    back.button("Ajustar mis elecciones", key=prefix + "back2", on_click=_step, args=(prefix, 2), width="stretch")
    if confirm.button("Cruzar el umbral", icon=":material/swords:", key=prefix + "confirm", type="primary", width="stretch"):
        return dict(draft)
    return None
