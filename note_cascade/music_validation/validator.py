"""Ejecución de todas las comprobaciones y cálculo del estado (verde/amarillo/rojo)."""

from __future__ import annotations

from collections import defaultdict

from note_cascade.music_model.model import Score
from note_cascade.music_validation import checks
from note_cascade.music_validation.compare import ComparisonResult
from note_cascade.music_validation.issues import Issue, ValidationReport

# Umbrales de la puntuación ctx-grade de Audiveris
HIGH_CONFIDENCE = 0.75
LOW_CONFIDENCE = 0.55


def validate(score: Score, range_low: int = 21, range_high: int = 108, default_bpm: float = 100.0,
             extra_issues: list[Issue] | None = None, comparison: ComparisonResult | None = None,
             decisions: dict[str, str] | None = None, perf_notes: list[str] | None = None) -> ValidationReport:
    issues: list[Issue] = []
    issues += checks.check_time_signature_presence(score)
    issues += checks.check_measure_durations(score)
    issues += checks.check_durations(score)
    issues += checks.check_tuplets(score)
    issues += checks.check_ties(score)
    issues += checks.check_voice_overlaps(score)
    issues += checks.check_range(score, range_low, range_high)
    issues += checks.check_octave_leaps(score)
    issues += checks.check_accidentals(score)
    issues += checks.check_key_consistency(score)
    issues += checks.check_clefs(score)
    issues += checks.check_duplicates(score)
    issues += checks.check_hand_spans(score)
    issues += checks.check_hand_unisons(score)
    issues += checks.check_empty_measures(score)
    issues += checks.check_duplicate_systems(score)
    issues += checks.check_repeats(score, perf_notes or [])
    issues += checks.check_tempo(score, default_bpm)
    issues += checks.check_hands(score)
    if comparison is not None:
        issues += comparison.issues
    if extra_issues:
        issues += extra_issues
    # Sospechas de altura que la propia imagen desmiente: si todas las notas señaladas se comprobaron
    # en la imagen (cabeza a la altura de esa nota), la sospecha sobra.
    verified = {n.id for n in score.notes if n.image_verified or n.position_consistent}
    issues = [i for i in issues if not (i.code == "octava" and i.note_ids and set(i.note_ids) <= verified)]
    confirm_added_notes(score, issues)
    decisions = decisions or {}
    for issue in issues:
        st = decisions.get(issue.key())
        if st:
            issue.status = st
    order = {"error": 0, "aviso": 1, "info": 2}
    issues.sort(key=lambda i: (order.get(i.severity, 3), i.measure if i.measure is not None else -1))
    report = ValidationReport(issues=issues)
    compute_status(score, report, comparison)
    return report


CONFIRMED_IN_IMAGE = " Todas se comprobaron en la imagen: hay una cabeza a su altura, en su sitio y con su figura."


def confirm_added_notes(score: Score, issues: list[Issue]) -> None:
    """Notas que solo vio el segundo motor y que la imagen confirma una a una: la duda era si existían y a
    qué altura, y la imagen lo responde, así que el aviso pasa a ser informativo (no las marca «revisar»)."""
    image_ok = {n.id for n in score.notes if n.image_verified}
    for i in issues:
        if i.code == "complemento_motor" and i.severity == "aviso" and i.note_ids and set(i.note_ids) <= image_ok:
            i.severity = "info"
            if CONFIRMED_IN_IMAGE not in i.message:
                i.message += CONFIRMED_IN_IMAGE
        # Indicación de octava con rótulo y línea medidos en la imagen: comprobada (proyectos guardados antes
        # de que se creara directamente como información).
        if i.code == "octava_texto" and i.severity == "aviso" and "rótulo y línea medidos en la imagen" in i.message:
            i.severity = "info"


def compute_status(score: Score, report: ValidationReport, comparison: ComparisonResult | None = None) -> None:
    note_level: dict[str, str] = {}
    measure_level: dict[int, str] = defaultdict(lambda: "")
    rank = {"": 0, "ok": 1, "revisar": 2, "error": 3}

    def bump(d, k, v):
        if rank[v] > rank[d.get(k, "")]:
            d[k] = v

    for issue in report.issues:
        if issue.status != "abierta" or issue.severity == "info":
            continue
        level = "error" if issue.severity == "error" else "revisar"
        for nid in issue.note_ids:
            bump(note_level, nid, level)
        if issue.measure is not None:
            end = issue.measure_end if issue.measure_end is not None else issue.measure
            for mi in range(issue.measure, end + 1):
                bump(measure_level, mi, level)
    agreement = comparison.agreement if comparison else {}
    checked = 0
    ok_measures = 0
    for m in score.measures:
        checked += 1
        st = measure_level.get(m.index, "")
        notes = score.notes_in_measure(m.index)
        if not st:
            confs = [n.confidence for n in notes if n.confidence is not None]
            if agreement.get(m.index, 0) >= 0.85:
                st = "ok"
            elif confs and min(confs) >= LOW_CONFIDENCE and sum(confs) / len(confs) >= HIGH_CONFIDENCE:
                st = "ok"
            elif notes and all(n.source in ("importado", "usuario") or n.user_modified or n.image_verified
                               for n in notes if not n.rest):
                st = "ok"
            elif confs and min(confs) < LOW_CONFIDENCE:
                st = "revisar"
            else:
                st = "sin_datos"
        if st == "ok":
            ok_measures += 1
        report.measure_status[m.index] = st
        for n in notes:
            nst = note_level.get(n.id, "")
            if not nst:
                if n.user_modified or n.source in ("importado", "usuario") or n.image_verified:
                    nst = "ok"  # comprobada por el usuario o en la propia imagen
                elif n.confidence is not None and n.confidence < LOW_CONFIDENCE:
                    nst = "revisar"
                elif n.confidence is not None and n.confidence >= HIGH_CONFIDENCE:
                    nst = "ok"
                elif agreement.get(m.index, 0) >= 0.85:
                    nst = "ok"
                elif st in ("error", "revisar"):
                    nst = "revisar"
                else:
                    nst = "sin_datos"
            report.note_status[n.id] = nst
    report.measures_checked = checked
    report.measures_ok = ok_measures
