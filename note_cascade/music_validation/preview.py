"""Vista previa de una corrección: qué cambia antes de aceptarla.

La corrección se aplica sobre una copia de la partitura y se compara nota a nota con la actual. Nada se
modifica en la partitura abierta.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from note_cascade.music_model.model import Note, Score
from note_cascade.music_validation.fixes import apply_fix
from note_cascade.music_validation.issues import Fix

TYPE_ES = {"whole": "redonda", "half": "blanca", "quarter": "negra", "eighth": "corchea", "16th": "semicorchea",
           "32nd": "fusa", "64th": "semifusa", "breve": "cuadrada"}


@dataclass
class FixPreview:
    after: Score
    removed: list[Note] = field(default_factory=list)   # notas actuales que desaparecen o cambian
    added: list[Note] = field(default_factory=list)     # notas de la versión corregida nuevas o cambiadas
    lines: list[str] = field(default_factory=list)      # resumen legible
    measures: list[int] = field(default_factory=list)   # compases afectados
    message: str = ""


def _sig(n: Note) -> tuple:
    return (n.staff, n.voice, n.measure, n.onset, n.duration, n.rest, n.midi, n.hand, n.tie_start, n.tie_stop,
            tuple(n.articulations))


def _figure(n: Note) -> str:
    name = TYPE_ES.get(n.type_name or "", "") or f"{float(n.duration):g} tiempo(s)"
    return name + (" con puntillo" if n.dots == 1 else "")


def _describe(n: Note) -> str:
    what = "silencio" if n.rest else n.name()
    return f"{what} ({_figure(n)}, tiempo {float(n.onset) + 1:g}, pentagrama {n.staff})"


def preview_fix(score: Score, fix: Fix) -> FixPreview | None:
    if fix is None or fix.action == "delete_measures":
        return None   # los cambios de estructura no se pueden comparar compás a compás
    after = score.clone()
    message = apply_fix(after, fix)
    if len(after.measures) != len(score.measures):
        return None
    before_by_id = {n.id: n for n in score.notes}
    after_by_id = {n.id: n for n in after.notes}
    removed = [n for n in score.notes if n.id not in after_by_id or _sig(after_by_id[n.id]) != _sig(n)]
    added = [n for n in after.notes if n.id not in before_by_id or _sig(before_by_id[n.id]) != _sig(n)]
    lines: list[str] = []
    num = lambda mi: score.measures[mi].number  # noqa: E731
    for n in removed:
        if n.id in after_by_id:
            m = after_by_id[n.id]
            lines.append(f"Compás {num(n.measure)}: {_describe(n)} → {_describe(m)}")
    for n in removed:
        if n.id not in after_by_id:
            lines.append(f"Compás {num(n.measure)}: se quita {_describe(n)}")
    for n in added:
        if n.id not in before_by_id:
            lines.append(f"Compás {num(n.measure)}: se añade {_describe(n)}")
    # cambios que no son notas
    for mi, (a, b) in enumerate(zip(score.measures, after.measures)):
        if a.time_sig != b.time_sig and (a.time_sig_change or b.time_sig_change):
            lines.append(f"Compás {a.number}: compás {_ts(a.time_sig)} → {_ts(b.time_sig)}")
    before_tempos = {(t.measure, t.onset): t.bpm_quarter for t in score.tempos}
    for t in after.tempos:
        old = before_tempos.get((t.measure, t.onset))
        if old != t.bpm_quarter:
            lines.append(f"Compás {num(t.measure)}: tempo {old:g} → {t.bpm_quarter:g} negras por minuto"
                         if old and t.bpm_quarter else f"Compás {num(t.measure)}: cambia la indicación de tempo")
    clefs_a = {(c.staff, c.measure, c.onset, c.sign) for c in score.clefs}
    for c in after.clefs:
        if (c.staff, c.measure, c.onset, c.sign) not in clefs_a:
            lines.append(f"Compás {num(c.measure)}: clave de {'sol' if c.sign == 'G' else 'fa' if c.sign == 'F' else 'do'} "
                         f"en el pentagrama {c.staff}")
    keys_a = {(k.measure, k.fifths) for k in score.keys}
    for k in after.keys:
        if (k.measure, k.fifths) not in keys_a:
            lines.append(f"Compás {num(k.measure)}: armadura con {abs(k.fifths)} "
                         f"{'sostenido(s)' if k.fifths > 0 else 'bemol(es)' if k.fifths < 0 else 'alteraciones'}")
    measures = sorted({n.measure for n in removed + added})
    return FixPreview(after, removed, added, lines or ["La corrección no cambia ninguna nota."], measures, message)


def _ts(ts) -> str:
    return f"{ts[0]}/{ts[1]}" if ts else "sin indicar"
