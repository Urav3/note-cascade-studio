"""Unión de partituras reconocidas página a página."""

from __future__ import annotations

import copy

from note_cascade.music_model.model import Score


def concat_scores(scores: list[Score], warnings: list[str] | None = None) -> Score:
    """Concatena partituras (una por página) manteniendo la continuidad.

    Las partes se emparejan por orden. Si una página tiene más pentagramas que
    la primera se amplía la parte. Se informa de las discontinuidades
    sospechosas (cambio de armadura o de compás justo en el salto de página).
    """
    warnings = warnings if warnings is not None else []
    scores = [s for s in scores if s is not None and s.measures]
    if not scores:
        return Score()
    result = copy.deepcopy(scores[0])
    for page_no, s in enumerate(scores[1:], start=2):
        offset = len(result.measures)
        s = copy.deepcopy(s)
        while len(result.parts) < len(s.parts):
            result.parts.append(copy.deepcopy(s.parts[len(result.parts)]))
        for pi, part in enumerate(s.parts):
            if part.staves > result.parts[pi].staves:
                warnings.append(f"La página {page_no} tiene {part.staves} pentagramas en la parte {pi + 1} y la "
                                f"anterior {result.parts[pi].staves}.")
                result.parts[pi].staves = part.staves
            result.parts[pi].is_piano = result.parts[pi].is_piano or part.is_piano
        prev_ts = result.measures[-1].time_sig if result.measures else None
        first_ts = s.measures[0].time_sig
        if prev_ts and first_ts and prev_ts != first_ts and not s.measures[0].time_sig_change:
            warnings.append(f"El compás cambia de {prev_ts[0]}/{prev_ts[1]} a {first_ts[0]}/{first_ts[1]} al empezar "
                            f"la página {page_no}.")
        for pi in range(len(s.parts)):
            k_prev = result.key_at(pi, offset - 1)
            k_new = s.key_at(pi, 0)
            if k_prev != k_new:
                warnings.append(f"La armadura cambia al empezar la página {page_no} "
                                f"({k_prev:+d} → {k_new:+d} en el círculo de quintas): compruébalo.")
        for i, m in enumerate(s.measures):
            m.index = offset + i
            if i == 0:
                m.new_page = True
                m.new_system = True
            result.measures.append(m)
        for n in s.notes:
            n.measure += offset
            result.notes.append(n)
        for coll_name in ("clefs", "keys", "directions", "tempos", "chord_symbols"):
            for x in getattr(s, coll_name):
                x.measure += offset
                getattr(result, coll_name).append(x)
        if not result.title and s.title:
            result.title = s.title
    # Si el compás de la primera página no se leyó pero sí en otras, se propaga.
    current = None
    for m in result.measures:
        if m.time_sig:
            current = m.time_sig
        elif current:
            m.time_sig = current
    result.renumber_measures()
    for i, m in enumerate(result.measures):
        if not m.number or m.number.isdigit():
            m.number = str(i + 1)
    result.sort_notes()
    return result
