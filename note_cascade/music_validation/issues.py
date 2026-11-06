"""Avisos de validación y correcciones propuestas."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

SEVERITY_LABEL = {"error": "Error probable", "aviso": "Revisar", "info": "Información"}
SEVERITY_COLOR = {"error": "#e5484d", "aviso": "#f5a524", "info": "#4c9aff"}
EVIDENCE_LABEL = {"comprobado": "Comprobado", "sospecha": "Sospecha"}
CATEGORY_LABEL = {
    "ritmo": "Ritmo", "altura": "Altura y alteraciones", "estructura": "Estructura", "texto": "Texto",
    "motor": "Reconocimiento", "comparacion": "Comparación entre motores", "piano": "Piano (manos)",
}


@dataclass
class Fix:
    description: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"description": self.description, "action": self.action, "params": self.params}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Fix | None":
        if not d:
            return None
        return cls(d["description"], d["action"], dict(d.get("params", {})))


@dataclass
class Issue:
    severity: str                 # error | aviso | info
    category: str
    title: str
    message: str
    measure: int | None = None
    measure_end: int | None = None
    page: int | None = None
    note_ids: list[str] = field(default_factory=list)
    evidence: str = "sospecha"    # comprobado | sospecha
    fix: Fix | None = None
    status: str = "abierta"       # abierta | aceptada | rechazada | resuelta
    code: str = ""
    id: str = field(default_factory=lambda: "i" + uuid.uuid4().hex[:10])

    @property
    def severity_label(self) -> str:
        return SEVERITY_LABEL.get(self.severity, self.severity)

    def key(self) -> str:
        """Clave estable para recordar decisiones del usuario entre validaciones."""
        return f"{self.code}|{self.measure}|{','.join(sorted(self.note_ids))[:200]}"

    def to_dict(self) -> dict:
        return {"id": self.id, "severity": self.severity, "category": self.category, "title": self.title,
                "message": self.message, "measure": self.measure, "measure_end": self.measure_end,
                "page": self.page, "note_ids": self.note_ids, "evidence": self.evidence,
                "fix": self.fix.to_dict() if self.fix else None, "status": self.status, "code": self.code}

    @classmethod
    def from_dict(cls, d: dict) -> "Issue":
        return cls(d["severity"], d["category"], d["title"], d["message"], d.get("measure"), d.get("measure_end"),
                   d.get("page"), list(d.get("note_ids", [])), d.get("evidence", "sospecha"),
                   Fix.from_dict(d.get("fix")), d.get("status", "abierta"), d.get("code", ""),
                   d.get("id") or "i" + uuid.uuid4().hex[:10])


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)
    measures_checked: int = 0
    measures_ok: int = 0
    note_status: dict[str, str] = field(default_factory=dict)   # id -> ok | revisar | error | sin_datos
    measure_status: dict[int, str] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        c = {"error": 0, "aviso": 0, "info": 0}
        for i in self.issues:
            if i.status in ("abierta",):
                c[i.severity] = c.get(i.severity, 0) + 1
        return c

    def open_issues(self) -> list[Issue]:
        return [i for i in self.issues if i.status == "abierta"]
