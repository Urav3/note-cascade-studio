"""Comparación entre los resultados de dos motores OMR.

No se mezclan los resultados de forma indiscriminada: se alinean los compases
de ambos motores (alineamiento global tipo Needleman–Wunsch sobre la
similitud de alturas y ritmo) y, compás a compás:

* si coinciden, el acuerdo es evidencia real de que el compás es correcto;
* si discrepan, se avisa y se ofrece el compás del otro motor como corrección
  alternativa, que el usuario puede aceptar o rechazar.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction

from note_cascade.music_model.model import Note, Score
from note_cascade.music_validation.issues import Fix, Issue


@dataclass
class ComparisonResult:
    pairs: list[tuple[int | None, int | None]] = field(default_factory=list)
    agreement: dict[int, float] = field(default_factory=dict)   # compás principal -> similitud 0..1
    issues: list[Issue] = field(default_factory=list)
    agree_count: int = 0
    disagree_count: int = 0
    secondary_name: str = ""

    def summary(self) -> str:
        total = self.agree_count + self.disagree_count
        if not total:
            return "No se pudieron comparar los compases de los dos motores."
        return (f"{self.secondary_name}: coincide en {self.agree_count} de {total} compases comparables "
                f"({100 * self.agree_count / total:.0f} %).")


def _sig(score: Score, idx: int) -> tuple[Counter, Counter]:
    notes = [n for n in score.notes_in_measure(idx) if not n.rest and not n.grace]
    pitches = Counter(n.midi for n in notes)
    rhythm = Counter((n.onset, n.midi) for n in notes)
    return pitches, rhythm


def _jaccard(a: Counter, b: Counter) -> float:
    if not a and not b:
        return 1.0
    inter = sum((a & b).values())
    union = sum((a | b).values())
    return inter / union if union else 0.0


def align(a: Score, b: Score) -> list[tuple[int | None, int | None]]:
    na, nb = len(a.measures), len(b.measures)
    sa = [_sig(a, i)[0] for i in range(na)]
    sb = [_sig(b, j)[0] for j in range(nb)]
    gap = -0.4
    # Matriz de puntuación con banda para no explotar en partituras largas.
    band = max(12, abs(na - nb) + 8)
    NEG = -1e9
    score = [[NEG] * (nb + 1) for _ in range(na + 1)]
    back = [[0] * (nb + 1) for _ in range(na + 1)]
    score[0][0] = 0.0
    for i in range(1, na + 1):
        score[i][0] = i * gap
        back[i][0] = 1
    for j in range(1, nb + 1):
        score[0][j] = j * gap
        back[0][j] = 2
    for i in range(1, na + 1):
        jlo = max(1, int(i * nb / max(1, na)) - band)
        jhi = min(nb, int(i * nb / max(1, na)) + band)
        for j in range(jlo, jhi + 1):
            sim = _jaccard(sa[i - 1], sb[j - 1])
            best = score[i - 1][j - 1] + (sim * 2 - 0.5)
            bk = 0
            if score[i - 1][j] + gap > best:
                best, bk = score[i - 1][j] + gap, 1
            if score[i][j - 1] + gap > best:
                best, bk = score[i][j - 1] + gap, 2
            score[i][j] = best
            back[i][j] = bk
    pairs: list[tuple[int | None, int | None]] = []
    i, j = na, nb
    while i > 0 or j > 0:
        bk = back[i][j] if i > 0 and j > 0 else (1 if i > 0 else 2)
        if bk == 0:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif bk == 1:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    pairs.reverse()
    return pairs


def compare(primary: Score, secondary: Score, primary_name: str, secondary_name: str) -> ComparisonResult:
    res = ComparisonResult(secondary_name=secondary_name)
    res.pairs = align(primary, secondary)
    for i, j in res.pairs:
        if i is None or j is None:
            continue
        pa, ra = _sig(primary, i)
        pb, rb = _sig(secondary, j)
        if not pa and not pb:
            continue
        sim_r = _jaccard(ra, rb)
        sim_p = _jaccard(pa, pb)
        res.agreement[i] = sim_r
        if sim_r >= 0.85:
            res.agree_count += 1
        else:
            res.disagree_count += 1
            if sim_p < 0.7 or sim_r < 0.5:
                pcs_a = Counter(m % 12 for m in pa.elements())
                pcs_b = Counter(m % 12 for m in pb.elements())
                octave_only = _jaccard(pcs_a, pcs_b) > 0.9 and sim_p < 0.7
                m = primary.measures[i]
                detail = ("Las notas son las mismas pero en octavas distintas: comprueba las líneas adicionales."
                          if octave_only else
                          f"{primary_name} reconoció {sum(pa.values())} notas y {secondary_name} {sum(pb.values())}; "
                          f"solo coinciden el {100 * sim_r:.0f} % de las notas con su ritmo.")
                # Notas del principal confirmadas en la imagen que la otra versión no tiene: cambiar de versión
                # las borraría (p. ej. un motor que no ve una mano entera o no aplica una 8va).
                theirs = {(n.staff, n.onset, n.midi) for n in secondary.notes_in_measure(j, include_rests=False)}
                mine = [n for n in primary.notes_in_measure(i, include_rests=False)]
                lost = [n for n in mine if n.image_verified and (n.staff, n.onset, n.midi) not in theirs]
                all_verified = bool(mine) and all(n.image_verified for n in mine)
                if lost:
                    res.issues.append(Issue(
                        "info", "comparacion", f"Compás {m.number}: el otro motor lo lee distinto",
                        detail + f" Se mantiene la versión de {primary_name}: la de {secondary_name} perdería "
                        f"{len(lost)} nota(s) que están en la imagen.", measure=i, evidence="comprobado",
                        code="desacuerdo_descartado"))
                    continue
                res.issues.append(Issue(
                    "info" if all_verified else "aviso", "comparacion", f"Compás {m.number}: los motores no coinciden",
                    detail + " Escucha el compás y compara con la imagen; puedes aceptar la versión del otro motor.",
                    measure=i, note_ids=[n.id for n in primary.notes_in_measure(i)], evidence="comprobado",
                    code="desacuerdo",
                    fix=Fix(f"Usar la versión de {secondary_name} para este compás", "replace_measure",
                            {"measure": i, "notes": [_note_payload(n) for n in secondary.notes_in_measure(j)],
                             "engine": secondary_name})))
    total = res.agree_count + res.disagree_count
    if total >= 6 and res.agree_count / total < 0.35:
        # Discrepancia generalizada: avisar compás a compás sería ruido. Se resume en un aviso;
        # el acuerdo por compás se sigue usando para marcar en verde los que sí coinciden.
        res.issues = [Issue(
            "aviso", "comparacion", "Los dos motores discrepan en la mayoría de los compases",
            f"{primary_name} y {secondary_name} solo coinciden en {res.agree_count} de {total} compases. La imagen es "
            "difícil para el reconocimiento automático: revisa especialmente los compases marcados y escucha el "
            f"resultado. La versión de {secondary_name} se conserva como alternativa.",
            evidence="comprobado", code="desacuerdo_general")]
    extra_b = sum(1 for i, j in res.pairs if i is None)
    extra_a = sum(1 for i, j in res.pairs if j is None)
    if extra_a or extra_b:
        res.issues.append(Issue(
            "info", "comparacion", "Número de compases distinto entre motores",
            f"{primary_name}: {len(primary.measures)} compases; {secondary_name}: {len(secondary.measures)}. "
            f"{extra_a} compases solo existen en {primary_name} y {extra_b} solo en {secondary_name}.",
            evidence="comprobado", code="compases_distintos"))
    return res


def _note_payload(n: Note) -> dict:
    d = n.to_dict()
    d.pop("position", None)
    return d
