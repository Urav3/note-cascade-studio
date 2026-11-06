"""Vocabulario musical compartido por el lector MusicXML y el clasificador de texto."""

from __future__ import annotations

import re
import unicodedata

# Valores orientativos (negra por minuto). Se usan solo como *propuesta*:
# nunca se aplican sin avisar al usuario.
TEMPO_WORDS: dict[str, float] = {
    "larghissimo": 24, "grave": 35, "largo": 50, "lento": 52, "larghetto": 63, "adagio": 70,
    "adagietto": 74, "andante": 84, "andantino": 90, "andante moderato": 92, "moderato": 108,
    "allegretto": 112, "allegro moderato": 116, "allegro": 132, "vivace": 160, "vivacissimo": 172,
    "allegrissimo": 172, "presto": 184, "prestissimo": 200, "tempo di valse": 168,
    "maestoso": 88, "sostenuto": 66, "con moto": 112, "animato": 128, "tranquillo": 72,
    "langsam": 60, "mäßig": 100, "massig": 100, "schnell": 150, "lebhaft": 140,
    "lent": 60, "modéré": 100, "modere": 100, "vif": 150, "vite": 160,
    "slowly": 60, "slow": 60, "moderately": 100, "fast": 150, "quickly": 150, "lively": 140,
    "ballad": 72, "swing": 120, "rubato": 80,
}

TEMPO_CHANGE_WORDS = {
    "rit": "ritardando", "rit.": "ritardando", "ritard": "ritardando", "ritard.": "ritardando",
    "ritardando": "ritardando", "rall": "ritardando", "rall.": "ritardando", "rallentando": "ritardando",
    "allarg": "ritardando", "allarg.": "ritardando", "allargando": "ritardando",
    "ritenuto": "ritenuto", "riten.": "ritenuto", "rit": "ritardando",
    "accel": "accelerando", "accel.": "accelerando", "accelerando": "accelerando",
    "stringendo": "accelerando", "string.": "accelerando",
    "a tempo": "a_tempo", "tempo i": "a_tempo", "tempo primo": "a_tempo",
    "rubato": "rubato", "meno mosso": "meno_mosso", "più mosso": "piu_mosso", "piu mosso": "piu_mosso",
}

DYNAMIC_WORDS = {
    "pppp", "ppp", "pp", "p", "mp", "mf", "f", "ff", "fff", "ffff",
    "sf", "sfz", "sffz", "fz", "rfz", "rf", "sfp", "fp", "sfzp",
}

DYNAMIC_TEXT = {
    "cresc": "crescendo", "cresc.": "crescendo", "crescendo": "crescendo",
    "dim": "diminuendo", "dim.": "diminuendo", "diminuendo": "diminuendo",
    "decresc": "diminuendo", "decresc.": "diminuendo", "decrescendo": "diminuendo",
    "smorz.": "diminuendo", "smorzando": "diminuendo", "morendo": "diminuendo",
    "calando": "diminuendo", "perdendosi": "diminuendo",
}

EXPRESSION_WORDS = {
    "dolce", "espressivo", "espr.", "espress.", "cantabile", "legato", "leggiero", "leggero",
    "marcato", "staccato", "tenuto", "sempre", "poco", "molto", "con", "sotto", "voce",
    "agitato", "con brio", "con fuoco", "grazioso", "scherzando", "semplice", "sostenuto",
    "mezza voce", "sotto voce", "con pedale", "senza pedale", "una corda", "tre corde",
    "simile", "sim.", "ten.", "marc.", "stacc.", "legg.", "dolciss.", "dolcissimo",
    "appassionato", "brillante", "deciso", "energico", "giocoso", "lamentoso", "misterioso",
    "pesante", "risoluto", "tranquillo", "teneramente", "affettuoso", "calmo", "delicato",
}

NAVIGATION_PATTERNS = [
    (re.compile(r"^d\.?\s*c\.?\s*al\s+fine$", re.I), "dacapo_al_fine"),
    (re.compile(r"^d\.?\s*c\.?\s*al\s+coda$", re.I), "dacapo_al_coda"),
    (re.compile(r"^d\.?\s*s\.?\s*al\s+fine$", re.I), "dalsegno_al_fine"),
    (re.compile(r"^d\.?\s*s\.?\s*al\s+coda$", re.I), "dalsegno_al_coda"),
    (re.compile(r"^(d\.?\s*c\.?|da\s+capo)$", re.I), "dacapo"),
    (re.compile(r"^(d\.?\s*s\.?|dal\s+segno)$", re.I), "dalsegno"),
    (re.compile(r"^da\s+capo\s+al\s+fine$", re.I), "dacapo_al_fine"),
    (re.compile(r"^dal\s+segno\s+al\s+(fine)$", re.I), "dalsegno_al_fine"),
    (re.compile(r"^dal\s+segno\s+al\s+coda$", re.I), "dalsegno_al_coda"),
    (re.compile(r"^fine\.?$", re.I), "fine"),
    (re.compile(r"^(to|al)\s+coda$", re.I), "tocoda"),
    (re.compile(r"^coda$", re.I), "coda"),
    (re.compile(r"^segno$", re.I), "segno"),
]

METRONOME_RE = re.compile(
    r"(?P<unit>[♩♪𝅗𝅥𝅘𝅥𝅘𝅥𝅮qhe]|negra|blanca|corchea)?\s*(?P<dot>[.·])?\s*=\s*(?:c\.?\s*|ca\.?\s*|circa\s*)?(?P<bpm>\d{2,3})",
    re.I,
)
STANDALONE_BPM_RE = re.compile(r"^\(?\s*(?:c\.?\s*|ca\.?\s*)?(?P<bpm>\d{2,3})\s*\)?$")
MEASURE_NUMBER_RE = re.compile(r"^\d{1,4}$")
CHORD_SYMBOL_RE = re.compile(
    r"^(?P<root>[A-G])(?P<acc>[#b♯♭]?)"
    r"(?P<kind>(maj|ma|M|min|mi|m|dim|aug|sus|add|°|ø|\+|-)?\d{0,2}(sus\d|add\d{1,2}|[#b♯♭]\d{1,2}|maj\d{1,2})*)"
    r"(\((?P<ext>[^)]*)\))?"
    r"(/(?P<bass>[A-G])(?P<bassacc>[#b♯♭]?))?$"
)
REHEARSAL_RE = re.compile(r"^[A-Z]$|^[A-Z]\d$|^\d{1,2}$")
OCTAVE_LINE_RE = re.compile(r"^(8va|8vb|8va\s*bassa|15ma|15mb|8|loco)\.?$", re.I)
PEDAL_RE = re.compile(r"^(ped\.?|𝒫𝑒𝒹\.?|\*|✱|❉)$", re.I)
# El signo «Ped.» de las ediciones suele estar dibujado con letra adornada y el OCR lo lee como
# «Reo.», «Leo.», «zeo.», «人e0.»…; solo se acepta debajo del pentagrama en música de piano.
PEDAL_OCR_RE = re.compile(r"^(\S[eaoc][doaecq0]\.|[prlbkz][eaoc][doaecq0]\.?)$", re.I)
FINGERING_RE = re.compile(r"^[1-5](\s*[-–]\s*[1-5])?$")
PAGE_NUMBER_RE = re.compile(r"^(-\s*)?\d{1,3}(\s*-)?$|^(p\.|pág\.|page|seite)\s*\d+$", re.I)
DATE_RE = re.compile(r"\b(1[5-9]\d\d|20\d\d)\b")
COPYRIGHT_RE = re.compile(r"(©|\(c\)|copyright|all rights reserved|todos los derechos|edition|verlag|publisher|"
                          r"editorial|isbn|printed in|www\.|http|\.com|\.org|imslp|public domain|dominio público|"
                          r"op\.\s*\d+|bwv|k\.\s*\d+|hob\.|catalog|plate|\bno\.\s*\d+)", re.I)
INSTRUMENT_WORDS = {
    "piano", "pno", "pno.", "pf", "pf.", "klavier", "pianoforte", "violin", "vln", "vln.", "viola", "vla",
    "cello", "vc", "vc.", "flute", "fl", "fl.", "oboe", "ob", "ob.", "clarinet", "cl", "cl.", "bassoon",
    "trumpet", "tpt", "horn", "hn", "trombone", "tbn", "voice", "soprano", "alto", "tenor", "bass",
    "guitar", "gtr", "organ", "violín", "flauta", "clarinete", "trompeta", "voz", "guitarra", "órgano",
    "oboes", "oboi", "s.", "a.", "t.", "b.", "s", "a", "t", "b",
}


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").strip()
    return re.sub(r"\s+", " ", text)


def tempo_word_bpm(text: str) -> tuple[str, float] | None:
    """Si el texto contiene una palabra de tempo conocida devuelve (palabra, bpm típico)."""
    t = normalize_text(text).lower().strip(" .,:;()")
    best: tuple[str, float] | None = None
    for word, bpm in TEMPO_WORDS.items():
        if re.search(r"(^|\b)" + re.escape(word) + r"(\b|$)", t):
            if best is None or len(word) > len(best[0]):
                best = (word, bpm)
    return best


def tempo_change_kind(text: str) -> str | None:
    t = normalize_text(text).lower().strip(" ,:;()-_")
    t = re.sub(r"[\s\-_.]+$", "", t)
    for word, kind in TEMPO_CHANGE_WORDS.items():
        w = word.rstrip(".")
        if t == w or t == word or t.startswith(w + " ") or t.startswith(w + "."):
            return kind
    return None


def navigation_kind(text: str) -> str | None:
    t = normalize_text(text).strip(" ,;")
    for regex, kind in NAVIGATION_PATTERNS:
        if regex.match(t):
            return kind
    return None


def parse_metronome(text: str) -> tuple[str, int, float] | None:
    """Interpreta «♩ = 120», «♩. = 65» o «q = 90». Devuelve (unidad, puntillos, valor)."""
    m = METRONOME_RE.search(normalize_text(text))
    if not m:
        return None
    unit_raw = (m.group("unit") or "").lower()
    unit = {
        "♩": "quarter", "𝅘𝅥": "quarter", "q": "quarter", "negra": "quarter",
        "♪": "eighth", "𝅘𝅥𝅮": "eighth", "e": "eighth", "corchea": "eighth",
        "𝅗𝅥": "half", "h": "half", "blanca": "half",
    }.get(unit_raw, "quarter" if unit_raw == "" else "quarter")
    dots = 1 if m.group("dot") else 0
    return unit, dots, float(m.group("bpm"))


def beat_unit_quarters(unit: str, dots: int = 0) -> float:
    base = {"whole": 4.0, "half": 2.0, "quarter": 1.0, "eighth": 0.5, "16th": 0.25, "32nd": 0.125}.get(unit, 1.0)
    total, add = base, base
    for _ in range(dots):
        add /= 2
        total += add
    return total


def is_dynamic(text: str) -> bool:
    return normalize_text(text).lower().strip(".") in DYNAMIC_WORDS


def dynamic_text_kind(text: str) -> str | None:
    t = normalize_text(text).lower()
    for word, kind in DYNAMIC_TEXT.items():
        if t == word or t.startswith(word.rstrip(".") + " ") or t.startswith(word):
            return kind
    return None


def is_expression(text: str) -> bool:
    t = normalize_text(text).lower().strip(" .,")
    if t in EXPRESSION_WORDS:
        return True
    words = re.split(r"[\s,]+", t)
    return bool(words) and sum(1 for w in words if w.strip(".") in EXPRESSION_WORDS or w in EXPRESSION_WORDS) >= max(1, len(words) // 2)


def parse_chord_symbol(text: str) -> dict | None:
    t = normalize_text(text).replace("♯", "#").replace("♭", "b")
    if not t or len(t) > 12:
        return None
    m = CHORD_SYMBOL_RE.match(t)
    if not m:
        return None
    acc = {"#": 1, "b": -1, "": 0}[m.group("acc") or ""]
    kind_raw = (m.group("kind") or "") + (f"({m.group('ext')})" if m.group("ext") else "")
    bass = m.group("bass")
    bass_acc = {"#": 1, "b": -1, "": 0}[m.group("bassacc") or ""] if bass else 0
    return {"root_step": m.group("root"), "root_alter": acc, "kind": chord_kind(kind_raw),
            "kind_text": kind_raw, "bass_step": bass, "bass_alter": bass_acc}


def chord_kind(kind_raw: str) -> str:
    k = kind_raw.strip()
    if k in ("", "M", "maj", "ma"):
        return "major"
    if k in ("m", "min", "mi", "-"):
        return "minor"
    if k in ("7",):
        return "dominant"
    if k in ("maj7", "M7", "ma7", "Δ7", "Δ"):
        return "major-seventh"
    if k in ("m7", "min7", "mi7", "-7"):
        return "minor-seventh"
    if k in ("dim", "°", "o"):
        return "diminished"
    if k in ("dim7", "°7", "o7"):
        return "diminished-seventh"
    if k in ("m7b5", "ø", "ø7"):
        return "half-diminished"
    if k in ("aug", "+"):
        return "augmented"
    if k.startswith("sus4") or k == "sus":
        return "suspended-fourth"
    if k.startswith("sus2"):
        return "suspended-second"
    if k == "6":
        return "major-sixth"
    if k == "m6":
        return "minor-sixth"
    if k == "9":
        return "dominant-ninth"
    if k in ("maj9", "M9"):
        return "major-ninth"
    if k in ("m9",):
        return "minor-ninth"
    if k.startswith("m"):
        return "minor"
    if k and k[0].isdigit():
        return "dominant"
    return "major"


CHORD_INTERVALS = {
    "major": [0, 4, 7], "minor": [0, 3, 7], "dominant": [0, 4, 7, 10], "major-seventh": [0, 4, 7, 11],
    "minor-seventh": [0, 3, 7, 10], "diminished": [0, 3, 6], "diminished-seventh": [0, 3, 6, 9],
    "half-diminished": [0, 3, 6, 10], "augmented": [0, 4, 8], "suspended-fourth": [0, 5, 7],
    "suspended-second": [0, 2, 7], "major-sixth": [0, 4, 7, 9], "minor-sixth": [0, 3, 7, 9],
    "dominant-ninth": [0, 4, 7, 10, 14], "major-ninth": [0, 4, 7, 11, 14], "minor-ninth": [0, 3, 7, 10, 14],
}
