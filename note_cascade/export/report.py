"""Informe de reconocimiento en HTML (autónomo, en español)."""

from __future__ import annotations

import datetime as dt
import html
from collections import Counter
from pathlib import Path

from note_cascade import APP_NAME, VERSION_LABEL
from note_cascade.layout.text_classifier import CATEGORY_LABELS
from note_cascade.music_model import pitch as P
from note_cascade.music_validation.issues import CATEGORY_LABEL, EVIDENCE_LABEL, SEVERITY_LABEL
from note_cascade.project.project import Project

CSS = """
:root{--bg:#f7f7f9;--fg:#1d1d24;--muted:#5d6070;--card:#fff;--line:#e2e3ea;--err:#c62f35;--warn:#b26b00;--info:#2b67c4;--ok:#1f8a4c}
@media (prefers-color-scheme: dark){:root{--bg:#15161b;--fg:#ececf1;--muted:#a3a6b5;--card:#1e2028;--line:#30323d;--err:#ff6b70;--warn:#ffb547;--info:#7fb0ff;--ok:#4fd18b}}
body{font-family:'Segoe UI',system-ui,sans-serif;background:var(--bg);color:var(--fg);margin:0;padding:24px;line-height:1.45}
main{max-width:1050px;margin:auto}h1{margin:0 0 4px}h2{margin-top:32px;border-bottom:1px solid var(--line);padding-bottom:6px}
.muted{color:var(--muted)}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}.card b{font-size:1.5em;display:block}
table{border-collapse:collapse;width:100%;background:var(--card);border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:.93em}
th,td{text-align:left;padding:7px 9px;border-bottom:1px solid var(--line);vertical-align:top}th{background:rgba(127,127,127,.08)}
.sev-error{color:var(--err);font-weight:600}.sev-aviso{color:var(--warn);font-weight:600}.sev-info{color:var(--info)}
.ok{color:var(--ok)}.bad{color:var(--err)}code{font-size:.9em}ul{margin:4px 0 4px 18px;padding:0}
"""


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def write_report(project: Project, path: Path, perf, cfg) -> Path:
    s = project.score
    notes = s.pitched_notes()
    status = project.options.get("status", {})
    note_status = Counter(status.get("notes", {}).values())
    issues = project.issues
    open_issues = [i for i in issues if i.status == "abierta"]
    counts = Counter(i.severity for i in open_issues)
    rows = []
    rows.append(f"<h1>Informe de reconocimiento</h1><p class='muted'>{_e(APP_NAME)} {_e(VERSION_LABEL)} · "
                f"{dt.datetime.now():%d/%m/%Y %H:%M}</p>")
    rows.append("<div class='cards'>")
    for label, value in (("Título", s.title or "—"), ("Compases", len(s.measures)), ("Notas", len(notes)),
                         ("Duración", f"{perf.total_seconds / 60:.1f} min"),
                         ("Errores probables", counts.get("error", 0)), ("Para revisar", counts.get("aviso", 0))):
        rows.append(f"<div class='card'><span class='muted'>{_e(label)}</span><b>{_e(value)}</b></div>")
    rows.append("</div>")
    rows.append("<h2>Origen</h2><table>")
    rows.append(f"<tr><th>Archivo</th><td>{_e(project.source_path or project.name)}</td></tr>")
    rows.append(f"<tr><th>Tipo</th><td>{_e(project.source_kind)}</td></tr>")
    if s.composer:
        rows.append(f"<tr><th>Compositor</th><td>{_e(s.composer)}</td></tr>")
    parts = ", ".join(f"{p.name or 'Parte'} ({p.staves} pentagrama{'s' if p.staves > 1 else ''}"
                      f"{', piano' if p.is_piano else ''})" for p in s.parts)
    rows.append(f"<tr><th>Partes</th><td>{_e(parts)}</td></tr>")
    ts = sorted({m.time_sig for m in s.measures if m.time_sig})
    rows.append(f"<tr><th>Compás</th><td>{_e(', '.join(f'{a}/{b}' for a, b in ts) or 'no reconocido')}</td></tr>")
    keys = sorted({k.fifths for k in s.keys})
    rows.append(f"<tr><th>Armadura</th><td>{_e('; '.join(P.key_signature_text(k) for k in keys) or '—')}</td></tr>")
    tempos = [t for t in s.tempos if t.bpm_quarter]
    tempo_txt = "; ".join(f"compás {s.measures[t.measure].number}: {t.bpm_quarter:g} negras/min ({t.source})"
                          for t in tempos[:8]) or f"sin indicación (se usa {cfg.music.default_tempo_bpm:g})"
    rows.append(f"<tr><th>Tempo</th><td>{_e(tempo_txt)}</td></tr></table>")

    rows.append("<h2>Motores utilizados</h2><table><tr><th>Motor</th><th>Versión</th><th>Función</th><th>Resultado</th>"
                "<th>Tiempo</th><th>Posiciones</th></tr>")
    for r in project.engine_runs:
        res = "<span class='ok'>correcto</span>" if r.ok else "<span class='bad'>falló</span>"
        rows.append(f"<tr><td>{_e(r.engine_name)}</td><td>{_e(r.version)}</td><td>{_e(r.role)}</td><td>{res}<br>"
                    f"<span class='muted'>{_e(r.message)}</span></td><td>{r.duration_s:.0f} s</td>"
                    f"<td>{_e(r.positions or '—')}</td></tr>")
    if not project.engine_runs:
        rows.append("<tr><td colspan=6>Archivo estructurado importado directamente (sin reconocimiento óptico).</td></tr>")
    rows.append("</table>")

    rows.append("<h2>Estado de las notas</h2><p>")
    labels = {"ok": "verde (alta confianza o comprobada)", "revisar": "amarillo (revisar)",
              "error": "rojo (error probable)", "sin_datos": "sin datos de confianza del motor"}
    rows.append(" · ".join(f"{_e(labels.get(k, k))}: <b>{v}</b>" for k, v in note_status.items()) or "—")
    rows.append("</p><p class='muted'>La confianza solo se muestra cuando procede de señales reales: la puntuación "
                "interna de Audiveris, el acuerdo entre dos motores o la coherencia comprobada del compás. "
                "No se inventan porcentajes.</p>")

    rows.append("<h2>Avisos de validación</h2>")
    if issues:
        rows.append("<table><tr><th>Gravedad</th><th>Tipo</th><th>Aviso</th><th>Evidencia</th><th>Estado</th></tr>")
        for i in issues:
            fix = f"<br><i>Corrección propuesta: {_e(i.fix.description)}</i>" if i.fix else ""
            rows.append(f"<tr><td class='sev-{i.severity}'>{_e(SEVERITY_LABEL.get(i.severity))}</td>"
                        f"<td>{_e(CATEGORY_LABEL.get(i.category, i.category))}</td><td><b>{_e(i.title)}</b><br>"
                        f"{_e(i.message)}{fix}</td><td>{_e(EVIDENCE_LABEL.get(i.evidence, i.evidence))}</td>"
                        f"<td>{_e(i.status)}</td></tr>")
        rows.append("</table>")
    else:
        rows.append("<p class='ok'>No se encontraron incoherencias.</p>")

    if project.pages:
        rows.append("<h2>Páginas</h2><table><tr><th>Página</th><th>Tipo</th><th>Resolución</th><th>Preparación</th>"
                    "<th>Texto detectado</th></tr>")
        for p in project.pages:
            texts = Counter(t.get("category") for t in p.text)
            ttxt = ", ".join(f"{CATEGORY_LABELS.get(k, k)}: {v}" for k, v in texts.most_common()) or "—"
            steps = "<ul>" + "".join(f"<li>{_e(x)}</li>" for x in p.steps + p.warnings) + "</ul>"
            rows.append(f"<tr><td>{_e(p.label)}</td><td>{_e(p.quality_kind)}</td>"
                        f"<td>{_e(f'{p.dpi:.0f} ppp' if p.dpi else 'original')}</td><td>{steps}</td>"
                        f"<td>{_e(ttxt)}</td></tr>")
        rows.append("</table>")
        rows.append("<h2>Textos clasificados</h2><p class='muted'>Ningún texto se convierte en notas. Solo tempos, "
                    "cambios de tempo, dinámicas, navegación, 8va, pedal y compases de espera influyen en la "
                    "interpretación.</p><table><tr><th>Página</th><th>Texto</th><th>Clasificación</th><th>Motivo</th></tr>")
        for p in project.pages:
            for t in p.text:
                if t.get("category") == "simbolo_musical":
                    continue
                rows.append(f"<tr><td>{p.index + 1}</td><td>{_e(t.get('text'))}</td>"
                            f"<td>{_e(CATEGORY_LABELS.get(t.get('category'), t.get('category')))}"
                            f"{' · efecto musical' if t.get('musical') else ''}</td><td>{_e(t.get('reason'))}</td></tr>")
        rows.append("</table>")
    if perf.notes_info:
        rows.append("<h2>Interpretación</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in perf.notes_info) + "</ul>")
    rows.append("<h2>Registro</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in project.log[-80:]) + "</ul>")
    doc = (f"<!doctype html><html lang='es'><head><meta charset='utf-8'><title>Informe · {_e(s.title or project.name)}"
           f"</title><meta name='viewport' content='width=device-width, initial-scale=1'><style>{CSS}</style></head>"
           f"<body><main>{''.join(rows)}</main></body></html>")
    path.write_text(doc, encoding="utf-8")
    return path
