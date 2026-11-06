"""Clasificación contextual del texto de una página de partitura.

Cada texto (de la capa de texto del PDF, del OCR o de Audiveris) se clasifica
según **dónde** está respecto de los pentagramas y sistemas, su tamaño, su
alineación y su contenido. No se usa una lista de palabras prohibidas: la
misma letra «C» puede ser un cifrado, una marca de ensayo o parte de una
referencia según su posición; si no hay evidencia suficiente queda como
«dudoso» para revisión.

Ningún texto se convierte en notas. Solo algunas categorías tienen efecto
musical (tempo, cambios de tempo, dinámicas, navegación, 8va, pedal,
compases de espera y, si el usuario lo activa, cifrados).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Callable

from note_cascade.music_model import terms
from note_cascade.pdf_processing.pdf_document import TextSpan

CATEGORY_LABELS = {
    "titulo": "Título", "subtitulo": "Subtítulo", "compositor": "Compositor",
    "credito": "Arreglista / letrista / créditos", "instrumento": "Nombre de instrumento",
    "numero_pagina": "Número de página", "encabezado": "Encabezado o pie de página",
    "numero_compas": "Número de compás", "referencia": "Referencia editorial", "nota_editorial": "Nota del editor",
    "letra": "Letra de canción", "cifrado": "Cifrado de acorde", "tempo": "Indicación de tempo",
    "cambio_tempo": "Cambio de tempo (rit./accel.)", "caracter": "Indicación de carácter", "dinamica": "Dinámica",
    "ensayo": "Marca de ensayo", "navegacion": "Navegación (D.C., D.S., Coda, Fine)",
    "octava": "Indicación de octava (8va/8vb)", "pedal": "Pedal", "digitacion": "Digitación",
    "compases_espera": "Compases de espera", "compas": "Indicación de compás",
    "simbolo_musical": "Símbolo musical (no es texto)", "dudoso": "Dudoso: revisar",
}
MUSICAL_CATEGORIES = {"tempo", "cambio_tempo", "dinamica", "navegacion", "octava", "pedal", "compases_espera",
                      "cifrado", "compas"}


@dataclass
class NormStaff:
    x0: float
    x1: float
    top: float
    bottom: float
    interline: float  # normalizado a la altura de la página


@dataclass
class NormSystem:
    staves: list[int]
    x0: float
    x1: float
    y0: float
    y1: float
    barlines: list[float] = field(default_factory=list)


@dataclass
class TextElement:
    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    source: str
    category: str
    confidence: str = "media"      # alta | media | baja
    reason: str = ""
    musical: bool = False
    value: str = ""                 # p. ej. bpm, nombre de dinámica, tipo de navegación
    system: int | None = None
    ocr_score: float | None = None
    line_end_x: float | None = None   # fin de la línea discontinua (8va, rit.…)
    beat_figure: tuple | None = None  # figura de pulso leída en la imagen («♩.» = ("quarter", 1)); no se guarda

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("text", "page", "x0", "y0", "x1", "y1", "source", "category",
                                              "confidence", "reason", "musical", "value", "system", "ocr_score", "line_end_x")}

    @classmethod
    def from_dict(cls, d: dict) -> "TextElement":
        return cls(**{k: d.get(k) for k in ("text", "page", "x0", "y0", "x1", "y1", "source", "category",
                                             "confidence", "reason", "musical", "value", "system", "ocr_score", "line_end_x")})


def merge_words_into_lines(spans: list[TextSpan]) -> list[TextSpan]:
    """Une palabras contiguas de la misma línea (solo capa de texto PDF)."""
    spans = sorted(spans, key=lambda s: (round(s.cy, 3), s.x0))
    lines: list[TextSpan] = []
    for s in spans:
        if lines:
            last = lines[-1]
            h = max(last.height, s.height, 1e-4)
            same_line = abs(last.cy - s.cy) < 0.45 * h
            gap = s.x0 - last.x1
            similar_font = abs(last.size - s.size) <= 0.25 * max(last.size, s.size, 1e-6)
            if same_line and -0.2 * h <= gap <= 0.9 * h and similar_font and last.source == s.source:
                lines[-1] = TextSpan(last.text + " " + s.text, min(last.x0, s.x0), min(last.y0, s.y0),
                                     max(last.x1, s.x1), max(last.y1, s.y1), max(last.size, s.size),
                                     last.font, last.bold and s.bold, last.italic and s.italic, last.source,
                                     last.confidence)
                continue
        lines.append(s)
    return lines


def classify_page(spans: list[TextSpan], staves: list[NormStaff], systems: list[NormSystem], page: int,
                  page_aspect: float = 1.3, is_first_page: bool = True, multirest_at: Callable[[int, float], bool] | None = None,
                  piano_like: bool = False) -> list[TextElement]:
    elements: list[TextElement] = []
    if not spans:
        return elements
    il = statistics.median([s.interline for s in staves]) if staves else 0.01
    first_top = min((s.top for s in staves), default=1.0)
    last_bottom = max((s.bottom for s in staves), default=0.0)
    header_spans = [s for s in spans if not staves or s.y1 < first_top - 2.5 * il]
    header_sizes = sorted({round(s.height, 4) for s in header_spans if _alpha(s.text)}, reverse=True)
    lyric_rows = _lyric_rows(spans, staves, systems, il)

    for s in spans:
        text = terms.normalize_text(s.text)
        if not text:
            continue
        el = TextElement(text, page, s.x0, s.y0, s.x1, s.y1, s.source, "dudoso", ocr_score=s.confidence)
        _classify(el, s, staves, systems, il, first_top, last_bottom, header_sizes, is_first_page,
                  multirest_at, piano_like, lyric_rows, spans)
        el.musical = el.category in MUSICAL_CATEGORIES
        elements.append(el)
    _resolve_titles(elements)
    return elements


def _alpha(text: str) -> bool:
    return sum(c.isalpha() for c in text) >= 2


def _system_of(s: TextSpan, systems: list[NormSystem], staves: list[NormStaff], il: float) -> tuple[int | None, str]:
    """(índice de sistema, zona) con zona: encima | dentro | entre | debajo | izquierda | lejos."""
    best = None
    best_d = 1e9
    for i, sys in enumerate(systems):
        if sys.y0 - 7 * il <= s.cy <= sys.y1 + 7 * il:
            d = 0 if sys.y0 <= s.cy <= sys.y1 else min(abs(s.cy - sys.y0), abs(s.cy - sys.y1))
            if d < best_d:
                best, best_d = i, d
    if best is None:
        return None, "lejos"
    # Texto en el hueco entre dos sistemas: si está casi igual de cerca del sistema de abajo,
    # pertenece a ese (tempos, ensayos y dinámicas iniciales van encima de su sistema).
    if s.cy > systems[best].y1 and best + 1 < len(systems):
        nxt = systems[best + 1]
        d_up = s.cy - systems[best].y1
        d_down = nxt.y0 - s.cy
        if 0 < d_down <= d_up * 1.3:
            best = best + 1
    sys = systems[best]
    if s.x1 <= sys.x0 + 0.3 * il and sys.y0 - 2 * il <= s.cy <= sys.y1 + 2 * il:
        return best, "izquierda"
    for si in sys.staves:
        st = staves[si]
        if st.top - 0.4 * il <= s.cy <= st.bottom + 0.4 * il:
            return best, "dentro"
    if s.cy < sys.y0:
        return best, "encima"
    if s.cy > sys.y1:
        return best, "debajo"
    return best, "entre"


def _lyric_rows(spans: list[TextSpan], staves, systems, il) -> set[int]:
    """Índices de spans que forman filas de letra (varias sílabas alineadas bajo un pentagrama)."""
    rows: dict[tuple[int, int], list[int]] = {}
    for idx, s in enumerate(spans):
        if not _alpha(s.text) and not re.fullmatch(r"[A-Za-zÀ-ÿ'’]+[-–]?", s.text or ""):
            continue
        sys_idx, zone = _system_of(s, systems, staves, il)
        if sys_idx is None or zone not in ("debajo", "entre"):
            continue
        key = (sys_idx, int(round(s.cy / max(il, 1e-4) / 1.5)))
        rows.setdefault(key, []).append(idx)
    result: set[int] = set()
    for key, idxs in rows.items():
        words = [spans[i] for i in idxs]
        if len(words) >= 3 and not all(terms.is_expression(w.text) or terms.is_dynamic(w.text) for w in words):
            xs = sorted(w.cx for w in words)
            spread = xs[-1] - xs[0]
            if spread > 0.25:
                result.update(idxs)
    return {id(spans[i]) for i in result}


def _classify(el: TextElement, s: TextSpan, staves, systems, il, first_top, last_bottom, header_sizes,
              is_first_page, multirest_at, piano_like, lyric_rows, all_spans=()) -> None:
    text = el.text
    low = text.lower().strip()
    digits = re.fullmatch(r"\(?\d{1,4}\)?\.?", text) is not None
    sys_idx, zone = _system_of(s, systems, staves, il) if staves else (None, "lejos")
    el.system = sys_idx
    in_header = bool(staves) and s.y1 < first_top - 2.5 * il
    in_footer = bool(staves) and s.y0 > last_bottom + 3.5 * il
    if not staves:
        in_header = True

    def set_(cat: str, conf: str, reason: str, value: str = "") -> None:
        el.category, el.confidence, el.reason, el.value = cat, conf, reason, value

    # 0) Número de página en las esquinas o centrado en el margen superior/inferior
    if digits and (s.y1 < 0.07 or s.y0 > 0.93) and (s.x0 > 0.8 or abs(s.cx - 0.5) < 0.06 or
                                                      (s.x1 < 0.2 and not staves)):
        return set_("numero_pagina", "alta", "Número en el margen de la página: número de página.", text.strip("()."))

    # 1) Texto superpuesto a las líneas del pentagrama
    if zone == "dentro":
        if terms.is_dynamic(text):
            return set_("dinamica", "media", "Dinámica situada sobre el pentagrama.", low.strip("."))
        sysobj = systems[sys_idx] if sys_idx is not None else None
        if digits and sysobj is not None and s.cx < sysobj.x0 + 10 * il:
            return set_("compas", "baja", "Cifras al comienzo del pentagrama: probablemente la indicación de compás.",
                        text)
        return set_("simbolo_musical", "media" if s.source == "ocr" else "baja",
                    "Está dentro de las líneas del pentagrama: es un símbolo musical leído como texto, no se usa.")

    # 2) Vocabulario musical inequívoco
    nav = terms.navigation_kind(text)
    if nav:
        return set_("navegacion", "alta", "Indicación de navegación (repetición o salto).", nav)
    metro = terms.parse_metronome(text)
    if metro and "=" in text:
        unit, dots, per_min = metro
        if re.match(r"^\s*=", text):
            # «= 75» sin figura legible (la figura suele ser un dibujo): la unidad se deduce después del compás
            return set_("tempo", "media", f"Indicación metronómica ({per_min:g}); la figura de pulso no se pudo "
                                          "leer.", f"{per_min:g}")
        bpm = per_min * terms.beat_unit_quarters(unit, dots)
        return set_("tempo", "alta", f"Indicación metronómica ({per_min:g} por {_unit_es(unit, dots)}).", f"{bpm:g}")
    change = terms.tempo_change_kind(text)
    if change and not in_header and not in_footer:
        return set_("cambio_tempo", "alta", "Cambio progresivo de tempo.", change)
    if terms.is_dynamic(text) and not in_header:
        return set_("dinamica", "alta", "Dinámica.", low.strip("."))
    dk = terms.dynamic_text_kind(text)
    if dk and not in_header and not in_footer:
        return set_("dinamica", "alta", "Indicación de dinámica escrita.", dk)
    octave = re.match(r"^\(?(8va|8vb|15ma|15mb)(\s*bassa)?\)?(\s*[-–.·_]+)*\s*\.?$", low)
    if octave and not in_header:
        kind = octave.group(1) if not octave.group(2) else "8vb"
        return set_("octava", "alta", "Indicación de octava: las notas afectadas suenan una octava distinta.", kind)
    if terms.OCTAVE_LINE_RE.match(text) and text.strip() != "8" and not in_header:
        return set_("octava", "alta", "Indicación de octava: las notas afectadas suenan una octava distinta.", low)
    if terms.PEDAL_RE.match(text) and not in_header:
        return set_("pedal", "media", "Indicación de pedal de resonancia.", low)
    if zone == "debajo" and s.source == "ocr" and terms.PEDAL_OCR_RE.match(text) and piano_like:
        return set_("pedal", "baja", "Signo de pedal («Ped.») con letra adornada, leído aproximadamente por el OCR.",
                    "ped.")

    # 3) Cabecera de la primera página
    if in_header:
        if terms.COPYRIGHT_RE.search(text) and not terms.tempo_word_bpm(text):
            return set_("referencia", "alta", "Texto editorial (catálogo, editorial, derechos).")
        if digits and (s.y0 < 0.06 or s.y1 > 0.94):
            return set_("numero_pagina", "alta", "Número aislado en el margen superior: número de página.", text)
        value = re.sub(r"\D", "", text)
        if digits and staves and s.source == "pdf" and len(value) in (2, 3) and 30 <= int(value) <= 260 \
                and s.y1 > first_top - 6 * il:
            return set_("tempo", "baja", "Número de 2–3 cifras justo encima del primer sistema: posible valor "
                                         "metronómico (la figura de pulso no es texto y no se pudo leer).", value)
        tw = terms.tempo_word_bpm(text)
        if tw and staves and s.y1 > first_top - 6 * il and not terms.is_expression(text):
            return set_("tempo", "alta", f"Palabra de tempo «{tw[0]}» justo encima del primer sistema "
                                         "(sin valor metronómico).", "")
        if low.split(" ")[0].strip(".:") in terms.INSTRUMENT_WORDS or any(
                w.strip(".:,") in terms.INSTRUMENT_WORDS for w in low.split() if len(w) > 3):
            return set_("instrumento", "media", "Nombre de instrumento en la cabecera.")
        if re.search(r"\b(music|words|lyrics|arr|arranged|transcr|revised|edited|by|de|por|letra|música|"
                     r"compuesto|composed|text|paroles|musik|bearb)\b", low):
            if re.search(r"(music by|composed|comp\.|música de|musik)", low):
                return set_("compositor", "alta", "Crédito de composición.")
            return set_("credito", "alta", "Crédito (arreglo, letra, revisión).")
        if s.x0 > 0.55:
            return set_("compositor", "media", "Texto alineado a la derecha en la cabecera: compositor/autor.")
        if header_sizes and abs(round(s.height, 4) - header_sizes[0]) < 1e-4 and _alpha(text) and is_first_page:
            return set_("titulo", "media", "El texto más grande de la cabecera.")
        if len(header_sizes) > 1 and abs(round(s.height, 4) - header_sizes[1]) < 1e-4 and _alpha(text):
            return set_("subtitulo", "media", "Segundo texto más grande de la cabecera.")
        if abs(s.cx - 0.5) < 0.12 and _alpha(text) and is_first_page:
            return set_("subtitulo", "media", "Texto centrado en la cabecera, bajo el título.")
        if s.x1 < 0.45 and _alpha(text):
            return set_("credito", "baja", "Texto pequeño a la izquierda de la cabecera (dedicatoria/créditos).")
        if digits:
            return set_("numero_pagina", "baja", "Número en la cabecera.", text)
        return set_("dudoso", "baja", "Texto de cabecera sin función clara.")

    # 4) Pie de página
    if in_footer:
        if digits or terms.PAGE_NUMBER_RE.match(text):
            return set_("numero_pagina", "alta", "Número en el pie de página.", text)
        if re.match(r"^(\d\)|\*|†|‡|\(\d\))", text):
            return set_("nota_editorial", "alta", "Nota a pie de página del editor.")
        return set_("referencia", "media", "Texto del pie de página (editorial, número de plancha, derechos).")

    # 5) A la izquierda del sistema
    if zone == "izquierda":
        if digits:
            return set_("numero_compas", "media", "Número a la izquierda del sistema.", text)
        if low.strip(".: ") in terms.INSTRUMENT_WORDS or len(text) <= 6 or terms.INSTRUMENT_WORDS & set(
                w.strip(".:") for w in low.split()):
            return set_("instrumento", "alta", "Nombre (o abreviatura) del instrumento al comienzo del sistema.")
        return set_("referencia", "baja", "Texto a la izquierda del sistema.")

    sysobj = systems[sys_idx] if sys_idx is not None else None

    # 6) Números sueltos
    if digits and sysobj is not None and zone == "debajo" and sys_idx + 1 < len(systems):
        # Número en el hueco entre sistemas, separado del pentagrama de arriba: pertenece al de abajo
        # (p. ej. «♩.=75» situado sobre notas agudas del sistema siguiente).
        nxt = systems[sys_idx + 1]
        d_up = (s.cy - sysobj.y1) / il
        d_down = (nxt.y0 - s.cy) / il
        if d_up >= 3 and 0 < d_down <= 9:
            sys_idx += 1
            el.system = sys_idx
            sysobj = nxt
            zone = "encima"
    if digits and sysobj is not None:
        value = re.sub(r"\D", "", text)
        near_start = s.cx < sysobj.x0 + 4 * il
        if zone == "encima" and near_start and s.y1 > sysobj.y0 - 3.5 * il:
            return set_("numero_compas", "alta", "Número encima del comienzo del sistema: número de compás.", value)
        if zone == "encima" and multirest_at is not None and multirest_at(sys_idx, s.cx):
            return set_("compases_espera", "alta", "Número sobre un silencio de varios compases.", value)
        stacked = any(o is not s and re.fullmatch(r"\d{1,2}", (o.text or "").strip()) and abs(o.cx - s.cx) < 1.5 * il
                      and abs(o.cy - s.cy) < 3.0 * il for o in all_spans)
        if stacked or (len(value) == 1 and value in "12345" and zone in ("encima", "debajo", "entre")):
            gap = (sysobj.y0 - s.y1) if zone == "encima" else (s.y0 - sysobj.y1)
            if stacked or piano_like or gap < 3 * il:
                return set_("digitacion", "media", "Cifras junto a las notas (o apiladas): digitación, no afecta al "
                                                   "sonido.", value)
        if text.startswith("("):
            return set_("numero_compas", "media", "Número entre paréntesis encima del sistema: número de compás.",
                        value)
        if len(value) in (2, 3) and zone == "encima":
            v = int(value)
            if s.source == "pdf" and 30 <= v <= 260 and s.y1 > sysobj.y0 - 9 * il:
                return set_("tempo", "baja", "Número de 2–3 cifras de la capa de texto del PDF, encima del sistema: "
                                             "posible valor metronómico (la figura de pulso es un dibujo y no se "
                                             "pudo leer).", value)
            if v % 5 == 0 or s.bold:
                return set_("numero_compas", "media", "Número destacado encima del sistema (las ediciones numeran "
                                                      "los compases cada 5 o 10).", value)
        return set_("dudoso", "baja", "Número aislado sin función clara: no se usa.", value)

    # 7) Cifrado de acordes / marcas de ensayo
    if s.source == "ocr" and len(text) <= 2 and (s.confidence or 0) < 0.85:
        return set_("dudoso", "baja", "Uno o dos caracteres leídos por el OCR con poca seguridad: puede ser parte de "
                                      "un símbolo musical.")
    chord = terms.parse_chord_symbol(text)
    if chord and zone == "encima" and sysobj is not None:
        if re.fullmatch(r"[A-Z]", text) and (s.bold or s.height > 1.6 * il or s.cx < sysobj.x0 + 6 * il):
            return set_("ensayo", "media", "Letra mayúscula destacada al comienzo de una sección: marca de ensayo.",
                        text)
        if re.fullmatch(r"[A-G]", text):
            return set_("dudoso", "baja", f"«{text}» puede ser un cifrado de acorde o una marca de ensayo.", text)
        return set_("cifrado", "media", "Símbolo de acorde encima del pentagrama.", text)
    if re.fullmatch(r"[A-Z]\d?", text) and zone == "encima":
        return set_("ensayo", "media", "Letra de sección encima del sistema: marca de ensayo.", text)

    # 8) Letra de canción
    if id(s) in lyric_rows:
        return set_("letra", "alta", "Sílabas alineadas bajo el pentagrama: letra de la canción (no genera notas).")

    # 9) Indicaciones de carácter y tempo en el cuerpo de la página
    tw = terms.tempo_word_bpm(text)
    if tw and zone == "encima" and not terms.is_expression(text):
        return set_("tempo", "media", f"Palabra de tempo «{tw[0]}» encima del sistema (sin valor metronómico).")
    if terms.is_expression(text):
        return set_("caracter", "alta", "Indicación de carácter o expresión.")
    if low.strip(".:") in terms.INSTRUMENT_WORDS:
        return set_("instrumento", "media", "Nombre de instrumento.")
    if terms.COPYRIGHT_RE.search(text):
        return set_("referencia", "media", "Referencia editorial.")
    if zone in ("debajo", "entre") and _alpha(text):
        return set_("letra", "baja", "Palabra bajo el pentagrama: probablemente letra o indicación.")
    return set_("dudoso", "baja", "No hay evidencia suficiente para decidir su función.")


def _unit_es(unit: str, dots: int) -> str:
    name = {"quarter": "negra", "eighth": "corchea", "half": "blanca", "whole": "redonda"}.get(unit, unit)
    return name + (" con puntillo" if dots else "")


def _resolve_titles(elements: list[TextElement]) -> None:
    titles = [e for e in elements if e.category == "titulo"]
    if len(titles) > 1:
        # Un único título: el más alto en la página; los demás pasan a subtítulo.
        titles.sort(key=lambda e: e.y0)
        for e in titles[1:]:
            e.category = "subtitulo"
            e.reason = "Mismo tamaño que el título, debajo de él."


def staves_to_norm(processed_page) -> tuple[list[NormStaff], list[NormSystem]]:
    """Convierte la geometría (coordenadas de la imagen procesada) a coordenadas normalizadas."""
    staves = []
    geom = processed_page.geometry
    for st in geom.staves:
        x0, ytop = processed_page.processed_to_normalized(st.x0, st.y(0, st.x0))
        x1, ybot = processed_page.processed_to_normalized(st.x1, st.y(4, st.x1))
        _, ytop2 = processed_page.processed_to_normalized(st.x1, st.y(0, st.x1))
        _, ybot2 = processed_page.processed_to_normalized(st.x0, st.y(4, st.x0))
        top, bottom = min(ytop, ytop2), max(ybot, ybot2)
        staves.append(NormStaff(min(x0, x1), max(x0, x1), top, bottom, (bottom - top) / 4))
    systems = []
    for sy in geom.systems:
        x0, y0 = processed_page.processed_to_normalized(sy.x0, sy.y0)
        x1, y1 = processed_page.processed_to_normalized(sy.x1, sy.y1)
        bars = [processed_page.processed_to_normalized(b, (sy.y0 + sy.y1) / 2)[0] for b in sy.barlines]
        systems.append(NormSystem(list(sy.staves), min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1), bars))
    return staves, systems
