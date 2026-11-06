"""Registro de motores OMR, estado y selección automática."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from note_cascade.app.config import AppConfig
from note_cascade.omr.audiveris import AudiverisEngine
from note_cascade.omr.base import EngineStatus, OMREngine
from note_cascade.omr.homr_engine import HomrEngine
from note_cascade.omr.oemer_engine import OemerEngine

log = logging.getLogger(__name__)

ENGINE_ORDER = ("audiveris", "homr", "oemer")


def create_engines(cfg: AppConfig) -> dict[str, OMREngine]:
    return {
        "audiveris": AudiverisEngine(cfg.engines.audiveris_path, cfg.engines.tessdata_dir),
        "homr": HomrEngine(cfg.engines.homr_python),
        "oemer": OemerEngine(cfg.engines.oemer_command),
    }


def engine_statuses(engines: dict[str, OMREngine], refresh: bool = False) -> dict[str, EngineStatus]:
    result = {}
    for eid in ENGINE_ORDER:
        if eid in engines:
            try:
                result[eid] = engines[eid].status(refresh)
            except Exception as exc:  # un motor roto no debe impedir abrir la aplicación
                log.exception("Error comprobando el motor %s", eid)
                result[eid] = EngineStatus(eid, engines[eid].name, False, detail=f"error al comprobarlo: {exc}")
    return result


@dataclass
class EnginePlan:
    primary: str | None
    secondary: str | None
    reasons: list[str] = field(default_factory=list)
    cross_check: str = "auto"


def plan_engines(statuses: dict[str, EngineStatus], page_kinds: list[str], setting: str = "auto",
                 cross_check: str = "auto") -> EnginePlan:
    available = [e for e in ENGINE_ORDER if statuses.get(e) and statuses[e].available]
    plan = EnginePlan(None, None, cross_check=cross_check)
    if not available:
        plan.reasons.append("No hay ningún motor de reconocimiento disponible.")
        return plan
    photos = sum(1 for k in page_kinds if k == "foto")
    if setting in ENGINE_ORDER and setting != "auto":
        if setting in available:
            plan.primary = setting
            plan.reasons.append(f"Motor elegido manualmente: {statuses[setting].name}.")
        else:
            plan.reasons.append(f"El motor elegido ({setting}) no está disponible; se usa la selección automática.")
    if plan.primary is None:
        if photos > len(page_kinds) / 2 and "homr" in available:
            plan.primary = "homr"
            plan.reasons.append("Las páginas parecen fotografías: HOMR está entrenado con fotos de cámara.")
        elif "audiveris" in available:
            plan.primary = "audiveris"
            plan.reasons.append("Partitura impresa, escaneada o vectorial: Audiveris reconstruye compases, voces, "
                                "ligaduras, dinámicas y textos.")
        else:
            plan.primary = available[0]
            plan.reasons.append(f"Se usa {statuses[plan.primary].name}, el único motor disponible.")
    others = [e for e in available if e != plan.primary and e != "oemer"] or \
             [e for e in available if e != plan.primary]
    if cross_check != "nunca" and others:
        plan.secondary = others[0]
        if cross_check == "siempre":
            plan.reasons.append(f"Verificación cruzada con {statuses[plan.secondary].name} activada.")
        else:
            plan.reasons.append(f"{statuses[plan.secondary].name} se usará si el primer motor falla en alguna "
                                "página o si su resultado tiene dudas (compases incoherentes, notas de baja confianza, "
                                "sin indicación de compás); entonces completa las notas que falten.")
    return plan
