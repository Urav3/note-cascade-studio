"""Aplicación del texto clasificado a la partitura.

Solo se añade información cuando el motor no la aportó y siempre queda
registrada como aviso o información para que el usuario la vea; nunca se
modifican notas en silencio. Las indicaciones de octava («8va - - -») se
resuelven aparte, con la imagen como prueba (``omr/octave_lines.py``).
"""

from __future__ import annotations

import difflib
import re
from fractions import Fraction

from note_cascade.layout.text_classifier import TextElement
from note_cascade.music_model.model import ChordSymbol, Direction, Score, TempoMark
from note_cascade.music_model import terms
from note_cascade.music_model.timeline import measure_lengths
from note_cascade.music_validation.issues import Fix, Issue


def locate(score: Score, page: int, x: float, y: float, lengths=None,
           system: int | None = None) -> tuple[int, Fraction] | None:
    """Compás y posición aproximada (en negras) para un punto normalizado de una página.

    ``system`` es el sistema de la página al que el clasificador asignó el texto: se busca
    primero en ese sistema y, si ahí no encaja, en toda la página.
    """
    cands = [m for m in score.measures if m.position is not None and m.position.page == page]
    if not cands:
        return None
    if system is not None:
        groups: list[list] = []
        for m in cands:
            if m.new_system or not groups:
                groups.append([])
            groups[-1].append(m)
        if 0 <= system < len(groups):
            sub = _best_measure(groups[system], x, y, limit=1.0)  # el sistema ya lo decidió el clasificador
            if sub is not None:
                return _onset_in(score, sub, x, lengths)
    best = _best_measure(cands, x, y)
    if best is None:
        return None
    return _onset_in(score, best, x, lengths)


def _best_measure(cands, x: float, y: float, limit: float = 0.25):
    best = None
    best_score = 1e9
    for m in cands:
        p = m.position
        dy = 0.0 if p.y - 0.25 * p.h <= y <= p.y + 1.25 * p.h else min(abs(y - p.y), abs(y - (p.y + p.h)))
        dx = 0.0 if p.x <= x <= p.x + p.w else min(abs(x - p.x), abs(x - (p.x + p.w)))
        s = dy * 4 + dx
        if s < best_score:
            best, best_score = m, s
    return best if best is not None and best_score <= limit else None


def _onset_in(score: Score, best, x: float, lengths) -> tuple[int, Fraction]:
    lengths = lengths or measure_lengths(score)
    p = best.position
    frac = min(1.0, max(0.0, (x - p.x) / max(p.w, 1e-6)))
    onset = Fraction(frac * float(lengths[best.index])).limit_denominator(4)
    if frac < 0.25:
        onset = Fraction(0)
    return best.index, onset


def _similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _looks_garbled(title: str) -> bool:
    """Títulos ilegibles que a veces inventa el OCR de un motor (p. ej. «E f f f f e»)."""
    words = [w for w in re.split(r"\s+", title.strip()) if w]
    if not words:
        return True
    real = [w for w in words if sum(c.isalpha() for c in w) >= 3]
    return len(real) / len(words) < 0.5


def integrate_text(score: Score, elements: list[TextElement], chord_symbols: bool = False) -> list[Issue]:
    issues: list[Issue] = []
    lengths = measure_lengths(score)
    page_texts = [e.text for e in elements]

    # Metadatos: el título del motor solo se acepta si aparece en la página.
    titles = [e for e in elements if e.category == "titulo"]
    if titles:
        ours = titles[0].text.strip(" .")
        if not score.title or max((_similar(score.title, t) for t in page_texts), default=0) < 0.6:
            if score.title and score.title != ours:
                issues.append(Issue("info", "texto", "Título corregido",
                                    f"El motor propuso «{score.title}», que no aparece en la página; se usa «{ours}».",
                                    evidence="comprobado", code="titulo"))
            score.title = ours
    elif score.title and _looks_garbled(score.title) and \
            max((_similar(score.title, t) for t in page_texts), default=0) < 0.6 and page_texts:
        issues.append(Issue("info", "texto", "Título descartado",
                            f"El motor propuso el título «{score.title}», que no coincide con ningún texto de la "
                            "página; se deja sin título.", evidence="comprobado", code="titulo"))
        score.title = ""
    subs = [e for e in elements if e.category == "subtitulo"]
    if subs and not score.subtitle:
        score.subtitle = subs[0].text.strip(" .")
    comps = [e for e in elements if e.category == "compositor"]
    if comps and not score.composer:
        score.composer = comps[0].text.strip(" .")

    # Nombre de la parte: el OCR del motor a veces toma como nombre un texto editorial.
    instruments = [e for e in elements if e.category == "instrumento" and e.y1 < 0.3]
    if len(score.parts) == 1 and instruments:
        name = score.parts[0].name or ""
        if not name or terms.COPYRIGHT_RE.search(name) or re.search(r"\b(no\.?|library|nº)\b", name, re.I) \
                or sum(c.isdigit() for c in name) > 0:
            new = instruments[0].text.strip(" .:")
            issues.append(Issue("info", "texto", "Nombre del instrumento",
                                f"El motor llamó a la parte «{name}»; se usa «{new}», que aparece en la cabecera.",
                                evidence="comprobado", code="nombre_parte"))
            score.parts[0].name = new

    bare_tempos = [e for e in elements if e.category == "tempo" and e.value and "=" not in e.text]
    if len(bare_tempos) > 8:
        issues.append(Issue("aviso", "texto", "Demasiados números con aspecto de tempo",
                            f"Se encontraron {len(bare_tempos)} números sueltos que podrían ser indicaciones "
                            "metronómicas. Es poco probable que todos lo sean, así que no se aplica ninguno; usa el "
                            "tempo predeterminado o fíjalo en la revisión.", evidence="sospecha", code="tempos_dudosos"))
        elements = [e for e in elements if e not in bare_tempos]

    for e in elements:
        loc = locate(score, e.page, e.cx, e.cy, lengths, system=e.system)
        if e.category == "tempo":
            issues += _tempo_from_text(score, e, loc)
        elif e.category == "cambio_tempo" and loc:
            mi, onset = loc
            if not any(d.kind == "tempo_change" and abs(d.measure - mi) <= 0 for d in score.directions):
                score.directions.append(Direction("tempo_change", mi, onset, 0, None, e.value, text=e.text,
                                                  source="texto"))
                issues.append(Issue("info", "texto", f"Compás {score.measures[mi].number}: «{e.text}» añadido",
                                    "El motor no reconoció este cambio de tempo; se tomó del texto de la página.",
                                    measure=mi, evidence="comprobado", code="cambio_tempo_texto"))
        elif e.category == "octava":
            continue  # se resuelven con la imagen en omr/octave_lines.py
        elif e.category == "compases_espera" and loc:
            mi, _ = loc
            issues.append(Issue("info", "estructura", f"Compás {score.measures[mi].number}: silencio de {e.value} compases",
                                "Hay un silencio de varios compases. Comprueba que el resultado tenga ese número de "
                                "compases de espera.", measure=mi, page=e.page, evidence="comprobado",
                                code="multisilencio"))
        elif e.category == "cifrado" and chord_symbols and loc and not score.chord_symbols:
            parsed = terms.parse_chord_symbol(e.text)
            if parsed:
                mi, onset = loc
                score.chord_symbols.append(ChordSymbol(mi, onset, 0, parsed["root_step"], parsed["root_alter"],
                                                       parsed["kind"], e.text, parsed["bass_step"],
                                                       parsed["bass_alter"]))
    return issues


def _tempo_from_text(score: Score, e: TextElement, loc) -> list[Issue]:
    issues: list[Issue] = []
    if loc is None:
        return issues
    mi, onset = loc
    m = score.measures[mi]
    if m.position is not None and e.cx < m.position.x + 0.5 * m.position.w:
        onset = Fraction(0)  # las indicaciones de tempo suelen ir al comienzo del compás
    existing = [t for t in score.tempos if abs(t.measure - mi) <= 0]
    if e.value:
        value = float(e.value)
        metro = terms.parse_metronome(e.text) if ("=" in e.text and not re.match(r"^\s*=", e.text)) else None
        assumed_unit = ""
        seen_unit = ""
        if metro:
            bpm = value  # ya convertido a negras por el clasificador
            per_minute = metro[2]
            unit, dots = metro[0], metro[1]
        elif e.beat_figure:
            from note_cascade.layout.metronome_figure import figure_name, figure_quarters
            unit, dots = e.beat_figure
            per_minute = value
            bpm = value * figure_quarters(unit, dots)
            seen_unit = figure_name(unit, dots)
        else:
            ts = m.time_sig
            compound = bool(ts and ts[1] == 8 and ts[0] % 3 == 0 and ts[0] > 3)
            if ts is None:
                ts_guess = _guess_compound(score)
                compound = ts_guess
            unit, dots = ("quarter", 1) if compound else ("quarter", 0)
            per_minute = value
            bpm = value * (1.5 if compound else 1.0)
            assumed_unit = "negra con puntillo (compás compuesto)" if compound else "negra"
        with_bpm = [t for t in existing if t.bpm_quarter]
        if with_bpm:
            t = with_bpm[0]
            if abs(t.bpm_quarter - bpm) > 2 and t.source != "texto":
                issues.append(Issue("aviso", "texto", f"Compás {m.number}: tempo distinto en el texto",
                                    f"El motor leyó {t.bpm_quarter:g} negras por minuto y el texto de la página indica "
                                    f"«{e.text}» ({bpm:g} negras por minuto).", measure=mi, page=e.page,
                                    evidence="comprobado", code="tempo_discrepante",
                                    fix=Fix(f"Usar {bpm:g} (texto de la página)", "set_tempo",
                                            {"measure": mi, "onset": str(t.onset), "bpm": bpm, "text": e.text,
                                             "unit": unit, "dots": dots, "per_minute": per_minute})))
            return issues
        score.tempos.append(TempoMark(mi, onset, bpm, text=e.text if metro else f"= {e.value}", beat_unit=unit,
                                      beat_dots=dots, per_minute=per_minute, source="texto"))
        msg = f"El motor no reconoció la indicación metronómica del compás {m.number}; se tomó del texto («{e.text}»)."
        known_meter = bool(m.time_sig)
        if seen_unit:
            msg += (f" La figura de pulso es un dibujo; se leyó en la imagen: {seen_unit} = {value:g}, es decir "
                    f"{bpm:g} negras por minuto. Es lo que indica la partitura.")
            fix = None
            if (unit, dots) != ("quarter", 0):
                fix = Fix(f"Usar negra = {value:g} en lugar de lo escrito ("
                          + ("más lento" if value < bpm else "más rápido") + ")",
                          "set_tempo", {"measure": mi, "onset": str(onset), "bpm": value, "text": e.text,
                                        "unit": "quarter", "dots": 0, "per_minute": value})
            issues.append(Issue("info", "texto", f"Compás {m.number}: tempo tomado del texto", msg, measure=mi,
                                page=e.page, evidence="comprobado", code="tempo_texto", fix=fix))
            return issues
        if assumed_unit:
            msg += (f" La figura de pulso está dibujada y no se puede leer como texto: se supone {assumed_unit}, "
                    f"es decir {bpm:g} negras por minuto")
            msg += (", que es lo habitual en este compás. Normalmente no hay que cambiarlo." if known_meter
                    else ". Compruébalo escuchando.")
        alt_bpm = value if dots else value * 1.5
        slower = alt_bpm < bpm
        issues.append(Issue("info" if (not assumed_unit or known_meter) else "aviso", "texto",
                            f"Compás {m.number}: tempo tomado del texto",
                            msg, measure=mi, page=e.page, evidence="comprobado" if not assumed_unit else "sospecha",
                            code="tempo_texto",
                            fix=None if not assumed_unit else Fix(
                                (f"Cambiar a negra = {value:g}" if dots else f"Cambiar a negra con puntillo = {value:g}")
                                + (" (sonará más lento)" if slower else " (sonará más rápido)"),
                                "set_tempo", {"measure": mi, "onset": str(onset), "bpm": alt_bpm, "text": e.text,
                                              "unit": "quarter", "dots": 0 if dots else 1, "per_minute": value})))
    else:
        if not any(t.text for t in existing):
            score.tempos.append(TempoMark(mi, onset, None, text=e.text, source="texto"))
    return issues


def _guess_compound(score: Score) -> bool:
    from note_cascade.music_validation.checks import _guess_time_signature, inferred_measure_length
    mode = inferred_measure_length(score)
    if not mode:
        return False
    num, den = _guess_time_signature(score, mode)
    return den == 8 and num % 3 == 0 and num > 3
