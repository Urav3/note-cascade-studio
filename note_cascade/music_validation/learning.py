"""Aprender de las correcciones del usuario.

1. **Dentro de la partitura.** La música se repite mucho y el motor suele equivocarse igual en los
   compases que leyó igual. Cuando el usuario corrige un compás, se buscan los compases cuya lectura
   original del motor era idéntica y se propone aplicarles la misma corrección.
2. **Por tipo.** El usuario puede aceptar de una vez todas las correcciones del mismo tipo. Nada se
   aplica solo: aceptar una corrección para probarla no significa quererla siempre.
"""

from __future__ import annotations

from note_cascade.music_model.model import Note, Score
from note_cascade.music_validation.issues import Fix, Issue


# ---------------------------------------------------------------------------
# 1) Corrección repetible dentro de la partitura
# ---------------------------------------------------------------------------

def _signature(score: Score, mi: int) -> tuple:
    return tuple(sorted((n.staff, n.voice, n.onset, n.duration, n.rest, 0 if n.rest else n.midi, n.hand or "")
                        for n in score.notes_in_measure(mi)))


def _payload(n: Note) -> dict:
    d = n.to_dict()
    d.pop("position", None)
    return d


def repeated_correction_proposals(current: Score, original: Score | None) -> list[Issue]:
    if original is None or len(original.measures) != len(current.measures):
        return []
    n = len(current.measures)
    orig = [_signature(original, i) for i in range(n)]
    cur = [_signature(current, i) for i in range(n)]
    edited = [i for i in range(n) if orig[i] != cur[i]
              and any(x.user_modified for x in current.notes_in_measure(i))]
    issues: list[Issue] = []
    proposed: set[int] = set()
    for i in edited:
        if not orig[i]:
            continue
        for j in range(n):
            if j == i or j in proposed or orig[j] != orig[i] or cur[j] != orig[j] or cur[j] == cur[i]:
                continue
            proposed.add(j)
            notes = current.notes_in_measure(i)
            issues.append(Issue(
                "aviso", "aprendizaje", f"Compás {current.measures[j].number}: misma corrección que en el "
                                        f"{current.measures[i].number}",
                f"El motor leyó este compás exactamente igual que el compás {current.measures[i].number}, que "
                "corregiste. Probablemente tiene el mismo error: puedes aplicarle la misma corrección.",
                measure=j, note_ids=[x.id for x in current.notes_in_measure(j)], evidence="comprobado",
                code="correccion_repetible",
                fix=Fix(f"Aplicar la corrección del compás {current.measures[i].number}", "replace_measure",
                        {"measure": j, "notes": [_payload(x) for x in notes], "engine": "tu corrección",
                         "keep_hands": True})))
    return issues


# ---------------------------------------------------------------------------
# 2) Tipo de corrección (para «Aceptar todas las de este tipo»)
# ---------------------------------------------------------------------------

def decision_key(issue: Issue) -> str:
    """Tipo de corrección: el código del aviso y el verbo de la corrección («Desplazar», «Quitar»…),
    para no confundir aplicar algo con deshacerlo."""
    if issue.fix is None or not issue.code:
        return ""
    verb = issue.fix.description.split(" ", 1)[0].lower()
    return f"{issue.code}|{verb}"


# Correcciones que hay que decidir una a una (cada compás es distinto)
NO_BULK = {"desacuerdo", "correccion_repetible", "cabeza_sin_nota"}


def same_kind(issue: Issue, issues: list[Issue]) -> list[Issue]:
    """Avisos abiertos con el mismo tipo de corrección que ``issue`` (incluido él)."""
    key = decision_key(issue)
    if not key or issue.code in NO_BULK:
        return []
    return [i for i in issues if i.status == "abierta" and i.fix is not None and decision_key(i) == key]
