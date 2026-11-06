"""Genera docs/Simbolos_y_orden_de_lectura.pdf: qué símbolos reconoce Note Cascade Studio, qué efecto
tienen y en qué orden se lee una partitura. Uso: python scripts/make_symbols_pdf.py"""

from __future__ import annotations

from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "Simbolos_y_orden_de_lectura.pdf"

CSS = """
body { font-family: sans-serif; font-size: 9.5pt; color: #1d2230; }
h1 { font-size: 18pt; color: #4b3fd1; margin-bottom: 2pt; }
h2 { font-size: 13pt; color: #4b3fd1; margin-top: 12pt; border-bottom: 1px solid #c9c4f5; }
h3 { font-size: 10.5pt; margin-top: 8pt; color: #2a2f3d; }
p { margin: 3pt 0; }
li { margin: 1pt 0; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0; }
th { background-color: #ece9ff; text-align: left; padding: 2pt 4pt; font-size: 8.5pt; }
td { border-bottom: 1px solid #e3e5ec; padding: 2pt 4pt; font-size: 8.5pt; vertical-align: top; }
.small { font-size: 8pt; color: #5a6072; }
.box { background-color: #f4f3ff; padding: 5pt; border: 1px solid #d8d4fb; }
code { font-family: monospace; font-size: 8pt; }
"""

SOUND = "suena"
SEEN = "se ve / MusicXML"
WARN = "aviso"


def table(rows, head=("Símbolo", "Quién lo detecta", "Efecto", "Notas")) -> str:
    h = "".join(f"<th>{x}</th>" for x in head)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><tr>{h}</tr>{body}</table>"


M = "Motor (Audiveris; HOMR si hace falta)"
I = "Note Cascade (imagen)"
T = "Note Cascade (texto/OCR)"

HTML = f"""
<h1>Note Cascade Studio · Símbolos y orden de lectura</h1>
<p class="small">Generado a partir del código del programa (lector de MusicXML, comprobaciones con la imagen,
clasificador de texto e interpretación). «Suena» = cambia lo que se oye y el MIDI exportado; «se ve /
MusicXML» = se conserva y se muestra pero no cambia el sonido; «aviso» = se detecta para avisar o comprobar.</p>

<h2>1. ¿En qué orden lee el programa una partitura?</h2>
<div class="box"><p><b>Respuesta corta:</b> no lee de izquierda a derecha como una persona. Primero <b>detecta los
símbolos de toda la página con sus coordenadas</b> y después los <b>organiza</b>: sistemas de arriba abajo, compases
entre barras de compás (por su posición horizontal) y, dentro de cada compás, agrupa en <b>columnas de tiempo</b>
los símbolos alineados verticalmente y las ordena de izquierda a derecha; la duración de cada figura da el
momento de la siguiente.</p></div>
<h3>Prueba directa (archivo interno de Audiveris)</h3>
<p>El proyecto <code>.omr</code> que Audiveris deja junto a cada conversión guarda cada compás como un
<i>stack</i> con sus límites horizontales en píxeles y, dentro, <i>slots</i> (columnas de tiempo) con su posición
horizontal y su momento musical. Extracto real de «Sinner's Finale», página 2 (compás de 3/4):</p>
<p><code>&lt;slot x-offset="145" time-offset="0"/&gt; · &lt;slot x-offset="323" time-offset="1/4"/&gt; ·
&lt;slot x-offset="502" time-offset="1/2"/&gt;</code></p>
<p>Es decir: el orden en el tiempo se deduce de la posición horizontal de cada columna de símbolos.</p>
<h3>Pasos de Note Cascade Studio</h3>
<ol>
<li>Prepara cada página (orientación, inclinación, contraste) y conserva el original.</li>
<li>Detecta pentagramas (las 5 líneas), sistemas y barras de compás con sus coordenadas.</li>
<li>Ejecuta el motor de reconocimiento: <b>Audiveris</b> (principal) y, si hay dudas, <b>HOMR</b>.</li>
<li>Lee el resultado (MusicXML) compás a compás, en el orden del tiempo musical, y recupera la posición de
cada nota en la imagen (archivo <code>.omr</code>).</li>
<li>Comprueba con la propia imagen: claves, cabezas de nota, alturas, alteraciones, ligaduras, líneas de octava
y ritmo (el orden en el tiempo debe coincidir con el orden de izquierda a derecha).</li>
<li>Lee el texto de la página (tempo, rit., dinámicas, 8va, títulos…) y lo clasifica por su posición.</li>
<li>Valida el resultado y genera la interpretación que comparten el sonido, el MIDI y la cascada.</li>
</ol>
<h3>Cómo lo hacen otros programas</h3>
{table([
 ("Audiveris (código abierto)", "≈20 pasos sobre la hoja: pentagramas, cabezas (por plantillas en toda la hoja), plicas, barras, texto, compases, acordes, símbolos, ritmo.", "Coordenadas primero; luego compases y columnas de tiempo (lo confirma su <code>.omr</code>)."),
 ("oemer (código abierto)", "Dos redes neuronales segmentan la página entera (líneas, cabezas, claves, plicas…) y clasificadores identifican los símbolos.", "Coordenadas primero; después agrupa por pentagrama y posición."),
 ("HOMR (código abierto)", "Segmenta la página para encontrar los pentagramas; luego un modelo transformer lee cada pentagrama como una secuencia, de izquierda a derecha, y comprueba las alturas con las cabezas detectadas.", "Pentagrama a pentagrama, en secuencia."),
 ("PhotoScore, SmartScore, PlayScore (comerciales)", "No publican cómo funcionan.", "No se puede afirmar; no se hizo ingeniería inversa (sus licencias lo prohíben)."),
], head=("Programa", "Cómo reconoce", "Orden de lectura"))}
<h3>Teclado de la cascada comparado con Synthesia</h3>
<p>Synthesia permite ver solo las teclas que usa la canción («Song Only») o un teclado fijo; recomienda 88 teclas
porque con 61 no caben muchas piezas. Note Cascade hace lo mismo: «Teclado automático» va de la nota más grave a
la más aguda de la pieza (con 2 teclas de margen) y también ofrece 88, 76, 61 y 49 teclas. <b>Nunca cambia una nota
de octava para que quepa</b>: si eliges menos teclas, las notas que quedan fuera no se dibujan. Ejemplo: «The Pool»
va de Mi1 a Si6 y el teclado automático muestra 73 teclas (Re1–Re7).</p>
<p class="small">Fuentes: audiveris.github.io/audiveris/_pages/explanation/steps/README ·
audiveris.github.io/audiveris/_pages/guides/main/pipeline · pypi.org/project/oemer · github.com/liebharc/homr ·
synthesia.app/support/guide/firstSong · synthesia.app/keyboards/info</p>

<h2>2. Estructura de la partitura</h2>
{table([
 ("Pentagramas de 5 líneas, sistemas, llaves de piano", f"{I} y {M}", SEEN, "Base de todas las posiciones."),
 ("Barras de compás (simple, doble, final)", M, SEEN, "Se cuentan también en la imagen para comprobar los compases."),
 ("Barras de repetición (inicio y fin)", M, SOUND, "Se expanden al reproducir (opción «Expandir repeticiones»)."),
 ("Casillas de 1.ª y 2.ª vez", M, SOUND, "Solo si van con barra de repetición (si no, se descartan con aviso)."),
 ("Segno, Coda", M, SOUND, "Saltos D.S./Coda."),
 ("D.C., D.S., Fine, To Coda, al Fine, al Coda", f"{T} y {M}", SOUND, ""),
 ("Indicación de compás (4/4, 3/4, 12/8…), cambios de compás", f"{M} + {I}", SOUND, "Un compás de otra medida no leído se deduce (p. ej. 3/8 en «The Pool»)."),
 ("Sin compás (senza misura)", M, SEEN, ""),
 ("Armadura y cambios de armadura", M, SOUND, "Alteraciones de cada nota."),
 ("Claves de sol, fa y do; con 8 arriba/abajo", M, SOUND, "Definen la altura de cada posición del pentagrama."),
 ("Cambios de clave dentro del pentagrama (sol y fa)", f"{I} + {M}", SOUND, "Si el motor no los ve, se insertan y se recalculan las alturas."),
 ("Marcas de ensayo (A, B…)", f"{M} y {T}", SEEN, ""),
 ("Silencios de varios compases (barra gruesa con número)", f"{I} y {T}", WARN, "Se comprueba el número de compases."),
 ("Instrumentos transpositores", M, SOUND, "Altura sonora según la transposición."),
])}

<h2>3. Notas, ritmo y altura</h2>
{table([
 ("Cabezas de nota: negra, blanca, redonda", f"{M} + {I}", SOUND, "La imagen confirma cada nota y añade cabezas de acordes que el motor no vio."),
 ("Figuras: cuadrada, redonda, blanca, negra, corchea, semicorchea, fusa, semifusa", M, SOUND, "Duración."),
 ("Puntillos (uno o dos)", f"{M} + {I}", SOUND, "Un puntillo perdido se deduce si el compás no cuadra."),
 ("Silencios de todas las figuras y de compás completo", M, SOUND, "Tiempo sin sonido."),
 ("Acordes y varias voces por pentagrama", M, SOUND, ""),
 ("Grupos irregulares (tresillos, quintillos…)", M, SOUND, ""),
 ("Notas de adorno (apoyaturas, acciacaturas)", M, SOUND, "Se tocan brevemente antes del tiempo."),
 ("Notas pequeñas (cue)", M, SEEN, ""),
 ("Ligaduras de prolongación (incluso entre sistemas o páginas)", f"{M} + {I}", SOUND, "Una sola nota larga; las que el motor no ve se buscan en la imagen."),
 ("Ligaduras de expresión (legato)", M, SEEN, "No cambian el sonido."),
 ("Líneas adicionales (arriba y abajo)", f"{M} + {I}", SOUND, "La altura se comprueba con la posición de la cabeza."),
 ("Alteraciones: sostenido, bemol y becuadro (y su vigencia en el compás)", f"{M} + {I}", SOUND, "La imagen corrige las que el motor no aplicó."),
 ("Dobles sostenidos y dobles bemoles", M, SOUND, "No se comprueban en la imagen."),
 ("Líneas de octava 8va, 8vb, 8va bassa, 15ma, 15mb", f"{I} + {T} + {M}", SOUND, "Alcance medido en la imagen; solo al pentagrama que la lleva; nunca dos veces."),
])}

<h2>4. Articulación, ornamentos y expresión</h2>
{table([
 ("Staccato, spiccato", M, SOUND, "Nota a la mitad de su duración."),
 ("Staccatissimo", M, SOUND, "Nota al 30 % de su duración."),
 ("Acento", M, SOUND, "Más fuerte (+14 de velocidad MIDI)."),
 ("Marcato (acento fuerte)", M, SOUND, "Más fuerte (+20)."),
 ("Tenuto, respiración, cesura y demás articulaciones", M, SEEN, ""),
 ("Calderón", M, SOUND, "Alarga la nota (tempo a la mitad durante la nota)."),
 ("Trino, mordente, grupeto, trémolo", M, SEEN, "Se conservan; no se desarrollan en el MIDI."),
 ("Arpegio (línea ondulada delante del acorde)", M, SOUND, "De la nota más grave a la más aguda, muy seguidas; cada una se mantiene hasta el final del acorde. Si solo lo ve el segundo motor, se copia."),
 ("Arcos de violín, armónicos, snap pizzicato", M, SEEN, ""),
 ("Dinámicas pppp…ffff, fp, sf, sfz, sffz, fz, rfz, rf, sfp, sfzp", f"{M} y {T}", SOUND, "Volumen (velocidad MIDI)."),
 ("Reguladores (crescendo, diminuendo)", M, SOUND, "Cambio gradual de volumen."),
 ("Pedal de resonancia (Ped. / *)", f"{M} y {T}", SOUND, "Mantiene las notas; una tecla vuelta a tocar se corta igualmente."),
 ("Tempo metronómico (negra = 92, negra con puntillo = 65…)", f"{M}, {T} e {I}", SOUND, "Si la figura es un dibujo se lee en la imagen (negra, blanca, corchea, con o sin puntillo); si no se distingue, se supone la del compás."),
 ("rit., rall., accel., a tempo", f"{T} y {M}", SOUND, "Cambio gradual cuando se conoce el tempo de llegada."),
 ("Palabras de tempo y carácter (Allegro, dolce…)", T, WARN, "Sin número no se inventa un tempo."),
])}

<h2>5. Texto de la página (24 categorías)</h2>
<p>El texto se lee de la capa de texto del PDF y con OCR (encima, debajo y entre los pentagramas) y se clasifica
por su contenido y su posición: título, subtítulo, compositor, créditos (arreglista, letrista), nombre de
instrumento, número de página, encabezado o pie, número de compás, referencia editorial, nota del editor, letra
de canción, cifrado de acorde, tempo, cambio de tempo, carácter, dinámica, marca de ensayo, navegación (D.C., D.S.,
Coda, Fine), indicación de octava, pedal, digitación, compases de espera, indicación de compás, símbolo musical
leído como texto (se descarta) y dudoso. <b>Ningún texto crea notas</b>; los cifrados solo se convierten en notas si se
activa esa opción.</p>

<h2>6. Comprobaciones propias con la imagen</h2>
<ul>
<li>Formas de cabezas de nota, alteraciones y claves <b>aprendidas de la propia partitura</b> (de lo que el motor sí
leyó) y buscadas en todas las páginas.</li>
<li>Altura de cada nota según la clave y la posición de su cabeza (incluidas las líneas adicionales).</li>
<li>Líneas discontinuas de octava con su rótulo, también cuando cruzan de sistema o de página.</li>
<li>Arcos de ligadura entre notas iguales seguidas.</li>
<li>Orden en el tiempo frente a orden de izquierda a derecha, y espacio que ocupa cada figura.</li>
<li>Número de compases por sistema y silencios de varios compases (la armadura o el compás «de cortesía» tras la última
barra no cuenta como compás).</li>
<li>Sitio de las notas sin posición exacta, a partir de las notas del mismo tiempo en cualquier pentagrama del compás.</li>
<li>Alteraciones leídas por el motor como si fueran notas (se quitan con aviso y se pueden reponer).</li>
</ul>
<p class="small">Para ver por qué una nota suena a una altura: <i>Revisar → Nota → Diagnóstico de altura</i>.
Para revisar toda la partitura y exportar el registro: <i>Diagnóstico → Diagnóstico de alturas…</i></p>
"""


def build(out: Path = OUT) -> Path:
    story = pymupdf.Story(html=HTML, user_css=CSS)
    writer = pymupdf.DocumentWriter(str(out))
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (42, 42, -42, -42)
    more = True
    while more:
        dev = writer.begin_page(mediabox)
        more, _ = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return out


if __name__ == "__main__":
    print(build())
