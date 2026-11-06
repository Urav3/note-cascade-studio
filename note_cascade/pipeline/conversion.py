"""Orquestación de la conversión: documento → MusicXML → modelo validado.

Etapas (con progreso real por página y por paso del motor):

1. Preparación de páginas (rasterizado, análisis de calidad, correcciones).
2. Texto de cada página y clasificación contextual.
3. Reconocimiento con el motor principal elegido según el tipo de página.
4. Lectura del MusicXML, posiciones en la imagen y confianza (si existe).
5. Segundo motor solo cuando hay motivo: páginas fallidas, muchas
   incoherencias o verificación cruzada pedida por el usuario.
6. Validación musical y estados verde/amarillo/rojo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from note_cascade.app import paths
from note_cascade.app.config import AppConfig, MusicSettings, ProcessingSettings
from note_cascade.app.errors import CancelledError, EngineFailedError, NoteCascadeError
from note_cascade.image_processing.pipeline import PreprocessOptions, ProcessedPage, choose_dpi, preprocess
from note_cascade.importers import image_io
from note_cascade.importers.document import SourceDocument
from note_cascade.layout.integrate import integrate_text
from note_cascade.layout.page_analysis import classify_text
from note_cascade.music_model.combine import concat_scores
from note_cascade.music_model.hands import assign_hands
from note_cascade.music_model.model import Score
from note_cascade.music_model.timeline import PerformanceOptions, build_performance
from note_cascade.music_validation.compare import ComparisonResult, align, compare
from note_cascade.music_validation.learning import repeated_correction_proposals
from note_cascade.omr.clef_check import check_clefs
from note_cascade.omr.image_check import check_against_image
from note_cascade.omr.octave_lines import resolve_octaves
from note_cascade.music_validation.repairs import (adopt_time_signature, arpeggios_from_secondary,
                                                    complement_from_secondary,
                                                    repair_bogus_endings, repair_fermatas_with_secondary,
                                                    repair_duration_by_spacing, repair_measure_rests,
                                                    repair_measure_time_signatures, repair_missing_dots,
                                                    repair_tied_accidentals, repair_voice_alignment,
                                                    repair_voice_overflow)
from note_cascade.music_validation.issues import Issue
from note_cascade.music_validation.validator import validate
from note_cascade.musicxml.reader import ReadResult, read_musicxml
from note_cascade.omr import audiveris_omr
from note_cascade.omr.base import CancelToken, EnginePageInput, EngineRequest, EngineResult, OMREngine
from note_cascade.omr.positions import approximate_positions, drop_courtesy_span
from note_cascade.omr.registry import engine_statuses, plan_engines
from note_cascade.project.project import EngineRunRecord, PageRecord, Project

log = logging.getLogger(__name__)

ProgressFn = Callable[[float, str], None]


@dataclass
class ConversionOptions:
    processing: ProcessingSettings = field(default_factory=ProcessingSettings)
    music: MusicSettings = field(default_factory=MusicSettings)
    pages: list[int] | None = None          # subconjunto de páginas (índices) o None = todas
    use_cache: bool = True

    @classmethod
    def from_config(cls, cfg: AppConfig) -> "ConversionOptions":
        import copy
        return cls(copy.deepcopy(cfg.processing), copy.deepcopy(cfg.music))


class ConversionPipeline:
    def __init__(self, cfg: AppConfig, engines: dict[str, OMREngine], progress: ProgressFn | None = None,
                 cancel: CancelToken | None = None, log_fn: Callable[[str], None] | None = None) -> None:
        self.cfg = cfg
        self.engines = engines
        self._progress = progress
        self.cancel = cancel or CancelToken()
        self._log = log_fn
        self._range = (0.0, 1.0)

    # ------------------------------------------------------------------ utilidades
    def stage(self, start: float, end: float) -> None:
        self._range = (start, end)

    def report(self, frac: float, message: str) -> None:
        a, b = self._range
        if self._progress:
            self._progress(a + (b - a) * max(0.0, min(1.0, frac)), message)

    def note(self, project: Project, text: str) -> None:
        project.add_log(text)
        log.info(text)
        if self._log:
            self._log(text)

    # ------------------------------------------------------------------ entrada
    def convert(self, doc: SourceDocument, options: ConversionOptions) -> Project:
        if doc.is_structured:
            return self.import_structured(doc, options)
        if not doc.needs_omr:
            raise NoteCascadeError("Este archivo no se puede convertir.")
        t0 = time.monotonic()
        project = Project.new_workspace(doc.name)
        project.source_path = str(doc.path) if doc.path else ""
        project.source_kind = doc.kind
        project.source_sha256 = doc.sha256
        project.options = {"processing": asdict(options.processing), "music": asdict(options.music)}
        for w in doc.warnings:
            self.note(project, w)
        page_ids = options.pages if options.pages is not None else list(range(len(doc.pages)))
        page_ids = [i for i in page_ids if 0 <= i < len(doc.pages)]
        if not page_ids:
            raise NoteCascadeError("No hay páginas para convertir.")

        # 1) Preparación de páginas
        self.stage(0.0, 0.15)
        processed: dict[int, ProcessedPage] = {}
        for k, idx in enumerate(page_ids):
            self.cancel.check()
            self.report(k / len(page_ids), f"Preparando {doc.pages[idx].label.lower()} ({k + 1} de {len(page_ids)})")
            proc, rec = self._prepare_page(doc, idx, options, project)
            processed[idx] = proc
            project.pages.append(rec)
        music_pages = [i for i in page_ids if processed[i].has_music]
        for i in page_ids:
            if not processed[i].has_music:
                self.note(project, f"{doc.pages[i].label}: no se encontraron pentagramas; se omite del reconocimiento.")
        if not music_pages:
            raise NoteCascadeError(
                "No se encontró notación musical en el documento.",
                "Comprueba que la imagen contenga pentagramas legibles. Si es una foto muy pequeña o borrosa, "
                "prueba con más resolución o con «Corrección de imagen: fuerte» en las opciones avanzadas.")

        # 2) Texto
        self.stage(0.15, 0.22)
        piano_like = any(len(s.staves) >= 2 for i in music_pages for s in processed[i].geometry.systems)
        all_text = []
        if options.processing.detect_text:
            for k, i in enumerate(page_ids):
                self.cancel.check()
                self.report(k / len(page_ids), f"Leyendo textos de {doc.pages[i].label.lower()}")
                rec = project.pages[k]
                try:
                    elements, origin = classify_text(doc.pages[i], processed[i], i, i == page_ids[0], True, piano_like)
                except Exception as exc:
                    log.exception("Fallo al clasificar texto")
                    elements, origin = [], "ninguno"
                    rec.warnings.append(f"No se pudo analizar el texto: {exc}")
                rec.text = [e.to_dict() for e in elements]
                rec.text_origin = origin
                all_text.extend(elements)

        # 3) Motores
        statuses = engine_statuses(self.engines)
        kinds = [processed[i].quality.kind for i in music_pages]
        plan = plan_engines(statuses, kinds, options.processing.engine, options.processing.cross_check)
        for r in plan.reasons:
            self.note(project, r)
        if plan.primary is None:
            raise NoteCascadeError(
                "No hay ningún motor de reconocimiento instalado.",
                "Ejecuta install_windows.bat o abre «Diagnóstico» para ver qué falta.")
        timeout = options.processing.engine_timeout_per_page * max(1, len(music_pages)) + 120
        self.stage(0.22, 0.75 if plan.secondary else 0.92)
        result, score, run = self._run_engine(plan.primary, "principal", project, processed, music_pages, timeout,
                                              options)
        comparison: ComparisonResult | None = None
        extra_issues: list[Issue] = []

        # 4) Segundo motor (respaldo / verificación)
        failed_pages = [p for p in music_pages if p not in run.pages_ok]
        need_secondary = False
        reason = ""
        if plan.secondary:
            if options.processing.cross_check == "siempre":
                need_secondary, reason = True, "verificación cruzada solicitada"
            elif options.processing.cross_check == "auto":
                if score is None:
                    need_secondary, reason = True, "el motor principal no produjo resultado"
                elif failed_pages:
                    need_secondary, reason = True, f"el motor principal falló en {len(failed_pages)} página(s)"
                else:
                    doubt = self._doubt_reason(score, options)
                    if doubt:
                        need_secondary, reason = True, doubt
        if need_secondary and plan.secondary:
            self.note(project, f"Se ejecuta {statuses[plan.secondary].name}: {reason}.")
            self.stage(0.75, 0.94)
            pages2 = music_pages if (score is not None and not failed_pages) or options.processing.cross_check == "siempre" \
                else (failed_pages or music_pages)
            if score is not None and options.processing.cross_check == "auto" and not failed_pages:
                pages2 = music_pages
            try:
                result2, score2, run2 = self._run_engine(plan.secondary, "verificación" if score else "respaldo",
                                                         project, processed, pages2, timeout, options)
            except CancelledError:
                raise
            except Exception as exc:
                log.exception("El motor secundario falló")
                self.note(project, f"El segundo motor falló: {exc}")
                score2 = None
            if score2 is not None:
                if score is None:
                    score = score2
                elif failed_pages and set(pages2) == set(failed_pages):
                    score = self._splice(score, score2, failed_pages, project)
                else:
                    project.alternative_scores[plan.secondary] = score2
                    name2 = statuses[plan.secondary].name
                    pairs = align(score, score2)
                    extra_issues += adopt_time_signature(score, score2, pairs, name2)
                    extra_issues += repair_fermatas_with_secondary(score, score2, pairs, name2)
                    added = complement_from_secondary(score, score2, pairs, name2)
                    extra_issues += added
                    extra_issues += arpeggios_from_secondary(score, score2, pairs, name2)
                    if added:
                        self.note(project, f"{name2} completó {sum(len(i.note_ids) for i in added)} nota(s) que el "
                                           f"motor principal no vio, en {len(added)} compás(es).")
                    comparison = compare(score, score2, statuses[plan.primary].name, name2)
                    self.note(project, comparison.summary())
        if score is None:
            raise EngineFailedError(
                "Ningún motor pudo reconocer la partitura.",
                "Prueba con otra corrección de imagen o más resolución (Opciones avanzadas) y vuelve a analizar. "
                "Si es una foto, asegúrate de que la hoja esté plana y bien iluminada.")

        # 5) Integración: manos, texto, validación
        self.stage(0.94, 1.0)
        self.report(0.1, "Asignando manos y aplicando el texto de la página")
        extra_issues += repair_bogus_endings(score)
        extra_issues += repair_measure_rests(score)
        extra_issues += repair_voice_overflow(score)
        extra_issues += repair_missing_dots(score)
        extra_issues += repair_duration_by_spacing(score)
        extra_issues += repair_voice_alignment(score)
        extra_issues += repair_measure_time_signatures(score)
        extra_issues += repair_tied_accidentals(score)
        self.report(0.2, "Comprobando las notas con la imagen")
        try:
            extra_issues += check_clefs(score, project)   # antes que nada: las alturas dependen de la clave
        except Exception as exc:  # auxiliar
            log.warning("No se pudieron comprobar las claves con la imagen: %s", exc)
        try:
            extra_issues += check_against_image(score, project)
        except Exception as exc:  # comprobación auxiliar: nunca debe impedir la conversión
            log.warning("No se pudo comprobar el resultado con la imagen: %s", exc)
        try:
            extra_issues += resolve_octaves(score, project)
        except Exception as exc:  # nunca debe impedir la conversión
            log.warning("No se pudieron resolver las indicaciones de octava: %s", exc)
        if comparison is not None and plan.secondary in project.alternative_scores:
            # Se vuelve a comparar con las notas ya comprobadas en la imagen y con las reparaciones hechas:
            # así no se propone una versión del otro motor que perdería notas que están en la imagen.
            comparison = compare(score, project.alternative_scores[plan.secondary], statuses[plan.primary].name,
                                 statuses[plan.secondary].name)
        if options.processing.piano_two_hands:
            assign_hands(score)
        if all_text:
            _read_beat_figures(all_text, processed)
            extra_issues += integrate_text(score, all_text, options.processing.chord_symbols_to_notes)
        for i in page_ids:
            if not processed[i].has_music and any(e.category not in ("simbolo_musical",) for e in
                                                  project.pages[page_ids.index(i)].text_elements()):
                extra_issues.append(Issue("info", "estructura", f"{doc.pages[i].label}: sin música",
                                          "Esta página no contiene pentagramas (portada, texto o página en blanco).",
                                          page=i, evidence="comprobado", code="pagina_sin_musica"))
        for p in failed_pages:
            if p not in [pg for r in project.engine_runs for pg in r.pages_ok]:
                extra_issues.append(Issue("error", "motor", f"Página {p + 1}: sin reconocer",
                                          "La página tiene pentagramas pero ningún motor pudo reconocerla. Falta esa "
                                          "música en el resultado.", page=p, evidence="comprobado",
                                          code="pagina_fallida"))
        extra_issues += self._structure_vs_image(score, processed)
        project.score = score
        project.original_score = score.clone()
        self.report(0.5, "Validando el resultado musical")
        self.revalidate(project, extra_issues, comparison)
        project.add_log(f"Conversión completa en {time.monotonic() - t0:.0f} s.")
        self.report(1.0, "Conversión terminada")
        project.dirty = True
        return project

    # ------------------------------------------------------------------
    def revalidate(self, project: Project, extra_issues: list[Issue] | None = None,
                   comparison: ComparisonResult | None = None) -> None:
        revalidate_project(project, self.cfg, extra_issues, comparison)

    def _doubt_reason(self, score: Score, options: ConversionOptions) -> str:
        """Motivo para ejecutar el segundo motor aunque el principal haya terminado («» si no hay)."""
        ratio = self._error_ratio(score, options)
        if ratio > 0.05:
            return f"el {100 * ratio:.0f} % de los compases tiene incoherencias rítmicas"
        if score.measures and not any(m.time_sig for m in score.measures):
            return "el motor principal no leyó la indicación de compás"
        notes = score.pitched_notes()
        low = [n for n in notes if n.confidence is not None and n.confidence < 0.6]
        if low:
            return f"el motor principal duda de {len(low)} nota(s) (suele pasar con redondas y acordes apilados)"
        empty = [m for m in score.measures if not score.notes_in_measure(m.index, include_rests=False)
                 and not any(n.measure_rest for n in score.notes_in_measure(m.index))]
        if empty:
            return f"{len(empty)} compás(es) sin notas"
        return ""

    def _error_ratio(self, score: Score, options: ConversionOptions) -> float:
        from note_cascade.music_validation import checks
        if not score.measures:
            return 1.0
        issues = checks.check_measure_durations(score)
        bad = {i.measure for i in issues if i.severity == "error"}
        return len(bad) / len(score.measures)

    # ------------------------------------------------------------------ páginas
    def _prepare_page(self, doc: SourceDocument, idx: int, options: ConversionOptions,
                      project: Project) -> tuple[ProcessedPage, PageRecord]:
        page = doc.pages[idx]
        dpi = choose_dpi(page.origin, page.kind_hint, page.native_dpi, options.processing.dpi)
        rgb = page.render(dpi)
        rerender = (lambda d, p=page: p.render(d)) if page.origin == "pdf" else None
        level = options.processing.image_correction
        proc = preprocess(rgb, idx, dpi, PreprocessOptions(level=level), rerender)
        orig_rel = f"paginas/pagina_{idx + 1:03d}_original.png"
        proc_rel = f"paginas/pagina_{idx + 1:03d}_procesada.png"
        image_io.save_png(proc.original, project.page_path(orig_rel))
        image_io.save_png(proc.image, project.page_path(proc_rel))
        rec = PageRecord(
            index=idx, label=page.label, kind_hint=page.kind_hint, quality_kind=proc.quality.kind,
            quality=proc.quality.to_dict(), dpi=proc.dpi, original_file=orig_rel, processed_file=proc_rel,
            matrix=proc.matrix.tolist(), original_size=proc.original_size,
            processed_size=(proc.image.shape[1], proc.image.shape[0]), geometry=proc.geometry.to_dict(),
            steps=list(proc.steps), warnings=list(proc.warnings), has_music=proc.has_music,
            processing_level=level)
        kind_es = {"limpia": "imagen limpia", "captura": "captura de pantalla", "escaneo": "escaneo",
                   "foto": "fotografía"}.get(proc.quality.kind, proc.quality.kind)
        if page.origin == "pdf" and page.kind_hint == "vector":
            kind_es = "PDF vectorial (exportado desde un programa de notación)"
        elif page.origin == "pdf" and page.kind_hint in ("raster", "mixed"):
            kind_es = f"PDF con imagen ({kind_es})"
        self.note(project, f"{page.label}: {kind_es}; {len(proc.geometry.staves)} pentagramas en "
                           f"{len(proc.geometry.systems)} sistemas.")
        return proc, rec

    # ------------------------------------------------------------------ motores
    def _run_engine(self, engine_id: str, role: str, project: Project, processed: dict[int, ProcessedPage],
                    pages: list[int], timeout: float, options: ConversionOptions
                    ) -> tuple[EngineResult | None, Score | None, EngineRunRecord]:
        engine = self.engines[engine_id]
        work = project.workspace / "motores"
        inputs = [EnginePageInput(p, project.workspace / f"paginas/pagina_{p + 1:03d}_procesada.png",
                                  processed[p].image.shape[1], processed[p].image.shape[0]) for p in pages]
        request = EngineRequest(inputs, work, project.name, timeout, self.report, self.cancel)
        st = engine.status()
        cache_key = _cache_key(engine_id, st.version, inputs)
        result = self._cache_load(engine_id, cache_key, request) if options.use_cache else None
        if result is not None:
            self.note(project, f"{engine.name}: se reutiliza un resultado anterior idéntico (mismas páginas y versión).")
        else:
            self.note(project, f"Reconociendo con {engine.name} {st.version} ({len(pages)} página(s))…")
            result = engine.recognize(request)
            if result.ok:
                self._cache_store(engine_id, cache_key, result)
        run = EngineRunRecord(engine_id, engine.name, result.version or st.version, role, result.ok,
                              result.message, result.duration_s, list(result.warnings))
        run.musicxml_files = [str(Path(p).relative_to(project.workspace)) if _is_relative(p, project.workspace)
                              else str(p) for p in result.musicxml_paths]
        run.log_file = str(result.log_path) if result.log_path else ""
        run.pages_ok = [pr.page_index for pr in result.page_results if pr.ok]
        run.pages_failed = [pr.page_index for pr in result.page_results if not pr.ok]
        project.engine_runs.append(run)
        self.note(project, result.message)
        for pr in result.page_results:
            if not pr.ok:
                self.note(project, f"{engine.name}, página {pr.page_index + 1}: {pr.message}")
        if not result.ok:
            return result, None, run
        try:
            score = self._score_from_result(engine_id, result, processed, project, run)
        except NoteCascadeError as exc:
            self.note(project, f"{engine.name}: {exc.message}")
            run.ok = False
            return result, None, run
        return result, score, run

    def _score_from_result(self, engine_id: str, result: EngineResult, processed: dict[int, ProcessedPage],
                           project: Project, run: EngineRunRecord) -> Score:
        source = engine_id
        if not result.per_page_files:
            reads = [read_musicxml(p, source=source) for p in result.musicxml_paths]
            warnings: list[str] = []
            score = concat_scores([r.score for r in reads], warnings) if len(reads) > 1 else reads[0].score
            for w in warnings:
                self.note(project, w)
            # Distribución de compases por página/sistema; con varios movimientos se encadenan las páginas.
            layout = []
            page_offset = 0
            for r in reads:
                for ml in r.measure_layout:
                    layout.append(type(ml)(ml.page + page_offset, ml.system, ml.system_in_page,
                                           ml.index_in_system, ml.width))
                if r.measure_layout:
                    page_offset += max(ml.page for ml in r.measure_layout) + 1
            if len(layout) != len(score.measures):
                layout = []
            pages_in_order = [pr.page_index for pr in result.page_results]
            xml_page_to_doc = list(run.pages_ok)
            positioned = False
            omr_path = result.extra.get("omr_path")
            if omr_path and Path(omr_path).exists():
                try:
                    omr_pages = audiveris_omr.parse_omr(omr_path)
                    sheet_to_page = {}
                    for k, page_index in enumerate(pages_in_order, start=1):
                        if page_index in processed:
                            sheet_to_page[k] = (page_index, processed[page_index])
                    xml_page_to_doc = [pages_in_order[p.sheet - 1] for p in omr_pages
                                       if p.sheet - 1 < len(pages_in_order)]
                    stats = audiveris_omr.apply_positions(score, omr_pages, sheet_to_page, layout)
                    self.note(project, stats.message)
                    positioned = stats.measures_mapped > 0
                    run.positions = "exactas" if positioned else ""
                    run.pages_ok = sorted(set(xml_page_to_doc))
                    run.pages_failed = [p for p in pages_in_order if p not in run.pages_ok]
                except Exception as exc:
                    log.exception("No se pudo leer el proyecto .omr")
                    self.note(project, f"No se pudieron leer las posiciones de Audiveris: {exc}")
            # Posiciones aproximadas para los compases que no se pudieron localizar con exactitud.
            ranges: dict[int, list[list[int]]] = {}
            for midx, ml in enumerate(layout):
                if ml.page < len(xml_page_to_doc):
                    doc_page = xml_page_to_doc[ml.page]
                    ranges.setdefault(doc_page, [])
                    while len(ranges[doc_page]) <= ml.system_in_page:
                        ranges[doc_page].append([])
                    ranges[doc_page][ml.system_in_page].append(midx)
                    if score.measures[midx].page is None:
                        score.measures[midx].page = doc_page
            approximate_positions(score, ranges, processed)
            if not positioned:
                run.positions = "aproximadas"
        else:
            scores = []
            for pr in result.page_results:
                if not pr.ok or pr.musicxml_path is None:
                    continue
                rr: ReadResult = read_musicxml(pr.musicxml_path, source=source)
                s = rr.score
                ranges: dict[int, list[list[int]]] = {pr.page_index: []}
                for midx, ml in enumerate(rr.measure_layout):
                    s.measures[midx].page = pr.page_index
                    while len(ranges[pr.page_index]) <= ml.system_in_page:
                        ranges[pr.page_index].append([])
                    ranges[pr.page_index][ml.system_in_page].append(midx)
                approximate_positions(s, ranges, processed)
                scores.append(s)
            warnings = []
            score = concat_scores(scores, warnings)
            for w in warnings:
                self.note(project, w)
            run.positions = "aproximadas"
        if not score.measures:
            raise NoteCascadeError("El motor no devolvió ningún compás.")
        score.metadata["engine"] = engine_id
        score.metadata["engine_version"] = result.version
        return score

    def _splice(self, primary: Score, secondary: Score, pages: list[int], project: Project) -> Score:
        """Inserta las páginas reconocidas por el segundo motor donde faltaban."""
        chunks: dict[int, Score] = {}
        all_pages = sorted({m.page for m in primary.measures if m.page is not None} | set(pages))
        for p in all_pages:
            src = secondary if p in pages else primary
            sub = _subscore(src, p)
            if sub.measures:
                chunks[p] = sub
        if not chunks:
            return primary
        self.note(project, f"Páginas {', '.join(str(p + 1) for p in pages)} completadas con el segundo motor.")
        return concat_scores([chunks[p] for p in sorted(chunks)])

    def _structure_vs_image(self, score: Score, processed: dict[int, ProcessedPage]) -> list[Issue]:
        """Compara compases por sistema del motor con las barras detectadas en la imagen."""
        issues = []
        by_page_sys: dict[tuple[int, int], list[int]] = {}
        sys_counter: dict[int, int] = {}
        current_page = None
        for m in score.measures:
            if m.page is None:
                continue
            if m.page != current_page:
                current_page = m.page
                sys_counter[m.page] = 0 if m.page not in sys_counter else sys_counter[m.page] + 1
            elif m.new_system:
                sys_counter[m.page] += 1
            by_page_sys.setdefault((m.page, sys_counter[m.page]), []).append(m.index)
        for (page, sidx), measures in by_page_sys.items():
            proc = processed.get(page)
            if proc is None or sidx >= len(proc.geometry.systems):
                continue
            g = proc.geometry
            spans = g.systems[sidx].measure_spans(g.interline * 1.5)
            spans = drop_courtesy_span(score, spans, measures[-1], g.interline, len(measures))
            if spans and abs(len(spans) - len(measures)) >= 1 and len(g.systems) == len(
                    {s for (p, s) in by_page_sys if p == page}):
                first = score.measures[measures[0]]
                issues.append(Issue("aviso", "estructura",
                                    f"Página {page + 1}, sistema {sidx + 1}: número de compases distinto",
                                    f"El reconocimiento tiene {len(measures)} compases en este sistema, pero en la imagen "
                                    f"se detectaron {len(spans)} barras de compás. Puede faltar o sobrar un compás (o la "
                                    "detección de barras puede haberse confundido con una plica).",
                                    measure=measures[0], measure_end=measures[-1], page=page, evidence="sospecha",
                                    code="barras_imagen"))
        return issues

    # ------------------------------------------------------------------ caché
    def _cache_dir(self, engine_id: str, key: str) -> Path:
        return paths.user_data_dir() / "cache" / engine_id / key

    def _cache_store(self, engine_id: str, key: str, result: EngineResult) -> None:
        try:
            d = self._cache_dir(engine_id, key)
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
            meta = {"version": result.version, "message": result.message, "warnings": result.warnings,
                    "per_page": result.per_page_files, "pages": [], "files": [], "omr": ""}
            for p in result.musicxml_paths:
                shutil.copy2(p, d / Path(p).name)
                meta["files"].append(Path(p).name)
            for pr in result.page_results:
                meta["pages"].append({"page": pr.page_index, "ok": pr.ok, "message": pr.message,
                                      "file": Path(pr.musicxml_path).name if pr.musicxml_path else ""})
            omr = result.extra.get("omr_path")
            if omr and Path(omr).exists():
                shutil.copy2(omr, d / Path(omr).name)
                meta["omr"] = Path(omr).name
            (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:
            log.warning("No se pudo guardar en caché: %s", exc)

    def _cache_load(self, engine_id: str, key: str, request: EngineRequest) -> EngineResult | None:
        d = self._cache_dir(engine_id, key)
        meta_file = d / "meta.json"
        if not meta_file.exists():
            return None
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
            target = request.work_dir / engine_id
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
            target.mkdir(parents=True, exist_ok=True)
            from note_cascade.omr.base import EnginePageResult
            engine = self.engines[engine_id]
            res = EngineResult(engine_id, engine.name, meta.get("version", ""), True,
                               per_page_files=meta.get("per_page", False))
            for f in meta["files"]:
                shutil.copy2(d / f, target / f)
                res.musicxml_paths.append(target / f)
            for p in meta["pages"]:
                res.page_results.append(EnginePageResult(p["page"], p["ok"],
                                                         (target / p["file"]) if p.get("file") else None,
                                                         p.get("message", "")))
            if meta.get("omr"):
                shutil.copy2(d / meta["omr"], target / meta["omr"])
                res.extra["omr_path"] = str(target / meta["omr"])
            res.message = (meta.get("message") or "") + " (resultado reutilizado)"
            res.warnings = meta.get("warnings", [])
            request.report(1.0, f"{engine.name} · resultado reutilizado")
            return res
        except (OSError, ValueError, KeyError) as exc:
            log.warning("Caché inválida para %s: %s", engine_id, exc)
            return None

    # ------------------------------------------------------------------ MusicXML / MIDI directos
    def import_structured(self, doc: SourceDocument, options: ConversionOptions) -> Project:
        project = Project.new_workspace(doc.name)
        project.source_path = str(doc.path) if doc.path else ""
        project.source_kind = doc.kind
        project.source_sha256 = doc.sha256
        project.options = {"processing": asdict(options.processing), "music": asdict(options.music)}
        self.stage(0.0, 1.0)
        self.report(0.2, "Leyendo el archivo")
        if doc.kind in ("musicxml", "mxl"):
            rr = read_musicxml(doc.path, source="importado")
            score = rr.score
            for w in rr.warnings:
                self.note(project, w)
            self.note(project, f"MusicXML importado directamente ({len(score.measures)} compases); "
                               "no se necesita reconocimiento óptico.")
        else:
            from note_cascade.midi.reader import read_midi
            score, notes = read_midi(doc.path)
            for w in notes:
                self.note(project, w)
            self.note(project, f"MIDI importado ({len(score.pitched_notes())} notas). Sirve para escuchar, ver en "
                               "cascada, editar y volver a exportar.")
        if options.processing.piano_two_hands:
            assign_hands(score)
        dest = project.workspace / "motores" / ("original" + doc.path.suffix.lower())
        shutil.copy2(doc.path, dest)
        project.score = score
        project.original_score = score.clone()
        self.report(0.8, "Validando")
        self.revalidate(project, [], None)
        self.report(1.0, "Listo")
        return project


def _read_beat_figures(elements, processed) -> None:
    """Figura de pulso dibujada junto a cada número de metrónomo («♩. = 65»): se lee en la imagen para no
    tener que suponerla."""
    from note_cascade.layout.metronome_figure import read_beat_figure
    for e in elements:
        if e.category != "tempo" or not e.value or "=" in e.text:
            continue
        proc = processed.get(e.page)
        if proc is None:
            continue
        try:
            e.beat_figure = read_beat_figure(proc.original, e.x0, e.y0, e.x1, e.y1)
        except Exception as exc:  # auxiliar: sin figura se sigue suponiendo la habitual del compás
            log.debug("No se pudo leer la figura del metrónomo: %s", exc)


_PERSISTENT_CODES = {"pagina_fallida", "pagina_sin_musica", "barras_imagen", "tempo_texto", "octava_texto",
                     "multisilencio", "titulo", "tempo_discrepante", "cambio_tempo_texto", "nombre_parte",
                     "tempos_dudosos", "complemento_motor", "compas_motor", "ligadura_alteracion",
                     "silencio_compas", "voz_separada", "nota_imagen",
                     "cabeza_sin_nota", "altura_imagen", "puntillo",
                     "voz_ajustada", "figura_espaciado", "cambio_compas",
                     "casillas_falsas", "alteracion_imagen", "ligadura_imagen", "calderon_descartado",
                     "octava_motor_dudosa", "clave_imagen", "nota_sin_imagen", "nota_sin_confirmar",
                     "alteracion_como_nota", "arpegio_motor"}
_COMPARISON_CODES = {"desacuerdo", "compases_distintos", "desacuerdo_general", "desacuerdo_descartado"}


def revalidate_project(project: Project, cfg: AppConfig, extra_issues: list[Issue] | None = None,
                       comparison: ComparisonResult | None = None) -> None:
    """Vuelve a validar la partitura actual (tras una edición o al cargar un proyecto).

    Conserva los avisos que no se pueden recalcular sin la imagen (texto, páginas fallidas…)
    y recalcula la comparación con el resultado alternativo del segundo motor si existe.
    """
    music = cfg.music
    perf = build_performance(project.score, PerformanceOptions(expand_repeats=True,
                                                               default_tempo=music.default_tempo_bpm))
    keep = [i for i in project.issues if i.code in _PERSISTENT_CODES] if extra_issues is None else []
    if comparison is None and project.alternative_scores:
        primary_name = project.engine_runs[0].engine_name if project.engine_runs else "Motor principal"
        for eid, alt in project.alternative_scores.items():
            name = next((r.engine_name for r in project.engine_runs if r.engine_id == eid), eid)
            comparison = compare(project.score, alt, primary_name, name)
            break
    learned = repeated_correction_proposals(project.score, project.original_score)
    report = validate(project.score, music.range_low, music.range_high, music.default_tempo_bpm,
                      extra_issues=(extra_issues or []) + keep + learned, comparison=comparison,
                      decisions=project.decisions, perf_notes=perf.notes_info)
    project.issues = report.issues
    project.options["status"] = {"notes": report.note_status,
                                 "measures": {str(k): v for k, v in report.measure_status.items()}}


def _cache_key(engine_id: str, version: str, inputs: list[EnginePageInput]) -> str:
    h = hashlib.sha256()
    h.update(f"{engine_id}|{version}|v1".encode())
    for p in inputs:
        h.update(str(p.page_index).encode())
        h.update(Path(p.image_path).read_bytes())
    return h.hexdigest()[:32]


def _is_relative(path, base: Path) -> bool:
    try:
        Path(path).relative_to(base)
        return True
    except ValueError:
        return False


def _subscore(score: Score, page: int) -> Score:
    import copy
    idxs = [m.index for m in score.measures if m.page == page]
    if not idxs:
        return Score(parts=copy.deepcopy(score.parts))
    lo, hi = min(idxs), max(idxs)
    sub = Score(parts=copy.deepcopy(score.parts), title=score.title)
    mapping = {}
    for new, old in enumerate(range(lo, hi + 1)):
        m = copy.deepcopy(score.measures[old])
        m.index = new
        sub.measures.append(m)
        mapping[old] = new
    for coll in ("notes", "directions", "tempos", "chord_symbols"):
        for x in getattr(score, coll):
            if lo <= x.measure <= hi:
                y = copy.deepcopy(x)
                y.measure = mapping[x.measure]
                getattr(sub, coll).append(y)
    for coll in ("clefs", "keys"):
        for x in getattr(score, coll):
            if x.measure <= hi:
                y = copy.deepcopy(x)
                y.measure = mapping.get(x.measure, 0)
                getattr(sub, coll).append(y)
    return sub
