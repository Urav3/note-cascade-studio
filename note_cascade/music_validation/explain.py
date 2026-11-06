"""Por qué una nota tiene el estado que tiene (verificada / revisar / error probable / sin datos).

El color de una nota sale de los avisos abiertos que la incluyen, de los avisos de su compás y de la
confianza del motor (ver ``validator.compute_status``). Aquí se explica lo mismo con palabras, para el
panel «Nota» de la revisión: cada motivo con su aviso, si lo hay, para poder abrirlo.
"""

from __future__ import annotations

from dataclasses import dataclass

from note_cascade.music_model.arpeggio import is_arpeggiated
from note_cascade.music_model.model import Note, Score
from note_cascade.music_validation.issues import Issue
from note_cascade.music_validation.validator import HIGH_CONFIDENCE, LOW_CONFIDENCE

STATUS_LABEL = {"ok": "verificada", "revisar": "revisar", "error": "error probable", "sin_datos": "sin datos"}
_COUNT_LABEL = {"ok": ("verificada", "verificadas"), "revisar": ("para revisar", "para revisar"),
                "error": ("con error probable", "con error probable"), "sin_datos": ("sin datos", "sin datos")}


def count_text(counts: dict[str, int]) -> str:
    """«3 verificadas · 2 para revisar» (en el orden error, revisar, sin datos, verificadas)."""
    order = ["error", "revisar", "sin_datos", "ok"]
    parts = []
    for k in sorted(counts, key=lambda k: order.index(k) if k in order else 9):
        one, many = _COUNT_LABEL.get(k, (k, k))
        parts.append(f"{counts[k]} {one if counts[k] == 1 else many}")
    return " · ".join(parts)


@dataclass
class Reason:
    level: str            # error | revisar | info | ok
    text: str
    issue: Issue | None = None


def _covers(issue: Issue, measure: int) -> bool:
    if issue.measure is None:
        return False
    end = issue.measure_end if issue.measure_end is not None else issue.measure
    return issue.measure <= measure <= end


def note_reasons(score: Score, issues: list[Issue], note: Note, status: str) -> list[Reason]:
    """Motivos del estado de ``note``, del más grave al informativo."""
    reasons: list[Reason] = []
    own = [i for i in issues if i.status == "abierta" and note.id in i.note_ids]
    for i in own:
        if i.severity in ("error", "aviso"):
            reasons.append(Reason("error" if i.severity == "error" else "revisar", i.title, i))
    if status in ("revisar", "error") and not reasons:
        if note.confidence is not None and note.confidence < LOW_CONFIDENCE:
            reasons.append(Reason("revisar", f"El motor la reconoció con poca seguridad (confianza "
                                             f"{note.confidence:.2f}) y no se pudo comprobar en la imagen."))
        else:
            # la nota no tiene datos propios (confianza intermedia o ninguna): hereda el estado de su compás
            for i in issues:
                if i.status == "abierta" and i.severity in ("error", "aviso") and _covers(i, note.measure):
                    reasons.append(Reason("error" if i.severity == "error" else "revisar",
                                          f"Aviso del compás: {i.title}", i))
            if not reasons:
                reasons.append(Reason("revisar", "Otras notas de este compás se reconocieron con poca seguridad y "
                                                 "esta no se pudo comprobar en la imagen."))
    if status == "ok":
        if note.user_modified:
            reasons.append(Reason("ok", "La has modificado tú."))
        elif note.image_verified:
            reasons.append(Reason("ok", "Comprobada en la imagen: hay una cabeza a esa altura, en su sitio."))
        elif note.source in ("importado", "usuario"):
            reasons.append(Reason("ok", "Viene de un archivo importado o la añadiste tú."))
        elif note.confidence is not None and note.confidence >= HIGH_CONFIDENCE:
            reasons.append(Reason("ok", f"El motor la reconoció con mucha seguridad (confianza {note.confidence:.2f})."))
        else:
            reasons.append(Reason("ok", "Los dos motores coinciden en este compás."))
    elif status == "sin_datos" and not reasons:
        reasons.append(Reason("info", "No hay con qué comprobarla: el motor no dio confianza y no se encontró su "
                                      "cabeza en la imagen. Compárala con la partitura."))
    for i in own:
        if i.severity == "info":
            reasons.append(Reason("info", i.title, i))
    if not note.rest and is_arpeggiated(score, note):
        reasons.append(Reason("info", "Acorde arpegiado (línea ondulada): se toca de la nota más grave a la más "
                                      "aguda, muy seguidas, y cada una se mantiene hasta el final del acorde."))
    if note.position is not None and note.position.approximate and status != "ok":
        reasons.append(Reason("info", "Su recuadro en la imagen es aproximado (el motor no dio su posición exacta)."))
    return reasons


def selection_reasons(score: Score, issues: list[Issue], notes: list[Note],
                      status_of) -> tuple[dict[str, int], list[Reason]]:
    """Para varias notas: cuántas hay de cada estado y los motivos sin repetir (el mismo aviso una vez)."""
    counts: dict[str, int] = {}
    seen: set[str] = set()
    out: list[Reason] = []
    for n in notes:
        st = status_of(n.id)
        counts[st] = counts.get(st, 0) + 1
        for r in note_reasons(score, issues, n, st):
            if r.level == "ok":
                continue
            key = r.issue.id if r.issue is not None else r.text
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    order = {"error": 0, "revisar": 1, "info": 2}
    out.sort(key=lambda r: order.get(r.level, 3))
    return counts, out
