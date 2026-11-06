# Note Cascade Studio

Conversor **local y gratuito** de partituras (PDF, fotos, capturas de pantalla) a **MIDI** editable, con
revisión asistida para quien no sabe leer partituras y un visualizador de notas **en cascada** al estilo de
los tutoriales de piano.

- Funciona sin conexión una vez instalado. Las partituras **nunca** se suben a internet.
- Sin suscripciones, claves de API ni límites de uso.
- Interfaz en español para Windows 10 y 11.

---

## 1. Instalación (Windows 10/11)

Requisitos: unos 3 GB libres y conexión a internet **solo durante la instalación**. No hacen falta
permisos de administrador ni tener Python instalado.

1. Copia la carpeta del proyecto donde quieras (por ejemplo `D:\Proyecto Piano`).
2. Haz doble clic en **`install_windows.bat`**.
   - Pregunta si quieres un acceso directo en el escritorio.
   - Descarga y verifica (SHA-256) desde los repositorios oficiales: el gestor `uv`, Python 3.12, las
     bibliotecas, **Audiveris 5.11** (portátil, en `tools\audiveris`), los idiomas de OCR para Audiveris y
     los **modelos de HOMR** (≈ 300 MB).
   - Al final ejecuta un diagnóstico y muestra qué está disponible.
3. Abre la aplicación con **`run_windows.bat`** (o con el acceso directo).

Todo queda dentro de la carpeta del proyecto (`tools\`, `.venv\`). Para desinstalar, borra la carpeta y,
si quieres, `%LOCALAPPDATA%\NoteCascadeStudio` (configuración, registros y carpetas de trabajo).

### Versión ejecutable (doble clic, sin Python)

Con la instalación hecha, ejecuta **`build_windows.bat`**. Genera `dist\NoteCascadeStudio\` con
`NoteCascadeStudio.exe` (y `NoteCascadeStudio-consola.exe` para la línea de órdenes), e incluye Audiveris,
los modelos de HOMR y los datos de OCR. Esa carpeta se puede copiar a otro equipo con Windows 10/11 de
64 bits. Ocupa ≈ 1 GB. Lee `LICENCIAS_TERCEROS.md` antes de distribuirla a otras personas.

---

## 2. Uso: cinco pasos

| Paso | Qué hace |
|---|---|
| **1 · Importar** | Arrastra archivos a la ventana, pulsa *Seleccionar archivos…* (puedes elegir varios) o pega una captura con **Ctrl+V**. Formatos: PDF, PNG, JPG/JPEG, WEBP, BMP, TIFF/TIF, MusicXML (.musicxml, .xml, .mxl) y MIDI. El tipo se detecta por el contenido, no solo por la extensión. |
| **2 · Analizar** | Prepara las páginas, lee los textos, reconoce la música y valida el resultado. Se ve el progreso real de cada etapa (página y paso del motor) y se puede **cancelar**. |
| **3 · Revisar** | Página original con las notas reconocidas superpuestas, lista de avisos con **correcciones propuestas** (aceptar / ignorar), propiedades de la nota y editor de rollo de piano. |
| **4 · Escuchar** | Reproducción local con el visualizador en cascada: teclas que se iluminan, colores por mano, velocidad, bucles, silenciar cada mano, rango del teclado. Junto al selector de teclado se indica cuántas teclas se ven y el registro real de la pieza: el teclado automático va de la nota más grave a la más aguda (nunca se cambia una nota de octava para que quepa). Si las manos se juntan en el teclado, **Separar manos donde se juntan** baja la izquierda una octava (o sube la derecha) solo en esos pasajes, para escuchar y ver; lo exportado lleva las notas escritas. **Ver partitura** abre una ventana pequeña con la página que resalta las notas que están sonando y el compás actual, y pasa de página sola. |
| **5 · Exportar** | MIDI tipo 1 (pistas por mano), MIDI tipo 0, MusicXML, informe HTML, proyecto `.ncsproj` y vídeo MP4 opcional. Cada archivo se vuelve a leer para comprobar que es válido. |

No es obligatorio revisar: tras analizar puedes pulsar **Exportar ahora**. La aplicación avisa si quedan
elementos dudosos.

**Varios archivos:** añade varios a la lista y pulsa *Convertir todos y exportar*: se procesan uno tras otro y
los resultados se guardan en la carpeta de salida (por defecto `Documentos\Note Cascade Studio`).

### Revisar sin saber leer partituras

- Cada nota es un rectángulo en el **editor de abajo**: cuanto más arriba, más aguda; cuanto más largo, más
  dura. Verde = mano derecha, azul = mano izquierda. Borde **amarillo** = revisar, **rojo** = error probable.
- Al seleccionar una nota se muestra su nombre (*Do♯4*), su número MIDI, si es tecla blanca o negra, su
  duración (*negra*, *corchea con puntillo*…), el compás y la mano, y se resalta en la **imagen original**.
- **Por qué está marcada:** debajo del nombre, la pestaña *Nota* explica su color: el aviso que la marca
  «revisar» (con *ver aviso* para abrirlo con su explicación y su corrección), la poca confianza del motor, o
  por qué está verificada (comprobada en la imagen, alta confianza, modificada por ti). Con varias notas
  seleccionadas, cuántas hay de cada estado y los avisos sin repetir.
- **Acordes arpegiados** (línea ondulada delante del acorde): se tocan de abajo arriba, como en el piano.
  *Arpegiar acorde* / *Quitar arpegio* en la pestaña *Nota* lo pone o lo quita.
- **Antes y después:** al seleccionar un aviso con corrección, el editor muestra el cambio sin aplicarlo (tachado
  en rojo lo que cambia, con contorno naranja cómo quedaría), el panel lo describe nota a nota y los botones
  *Antes* / *Después* tocan ese fragmento en las dos versiones.
- Seleccionar varias notas: **Ctrl+clic** (en la página o en el editor), **Mayús+arrastrar** en la página o
  arrastrar en un hueco del editor para seleccionar un área.
- *Escuchar compás* / *Escuchar estos compases* reproduce solo ese fragmento para compararlo con la imagen.
- Edición: doble clic = añadir nota · arrastrar = mover (altura y tiempo) · borde derecho = duración ·
  Supr = borrar · ↑/↓ = semitono (Mayús: octava) · H = cambiar de mano · Ctrl+Z / Ctrl+Y = deshacer/rehacer.
- En la pestaña *Nota* también se cambia la alteración, la duración, la mano, y desde el compás actual la
  **armadura**, la **clave**, el **tempo** o la **indicación de compás** (las notas se reinterpretan).
- La pestaña *Textos de la página* muestra cómo se clasificó cada texto (título, letra, tempo…).

### Colores de estado (sin porcentajes inventados)

| Color | Significado | De dónde sale |
|---|---|---|
| Verde | Verificada | Puntuación interna de Audiveris alta (`ctx-grade ≥ 0,75`), acuerdo entre dos motores, o dato estructurado/corregido por ti |
| Amarillo | Revisar | Aviso de validación (sospecha o incoherencia) o puntuación baja del motor |
| Rojo | Error probable | Incoherencia comprobada (p. ej. el compás tiene más tiempos de los que permite su indicación) |
| Gris | Sin datos | El motor no da confianza por nota (HOMR) y no hay otra evidencia: no se afirma nada |

---

## 3. Cómo funciona (resumen técnico)

```
PDF / imagen / captura ──► análisis de página (vectorial, escaneo, foto, en blanco)
   ► rasterizado a la resolución adecuada (300 ppp por defecto; más si el interlineado es pequeño)
   ► preparación de imagen según su tipo (perspectiva, fondo/sombras, ruido, contraste, inclinación,
     orientación 90°/180°, escala) — una imagen limpia no se toca
   ► detección propia de pentagramas, sistemas y barras de compás
   ► texto: capa de texto del PDF o OCR (RapidOCR) → clasificación contextual
   ► motor OMR (Audiveris o HOMR; segundo motor solo si hace falta) → MusicXML
   ► modelo musical interno + posiciones en la imagen + confianza real
   ► validación (ritmo, alturas, alteraciones, estructura, manos, comparación entre motores)
   ► revisión / reproducción / exportación MIDI y MusicXML verificadas
```

**Motores** (detalles en `docs/INVESTIGACION_MOTORES.md`):

- **Audiveris 5.11** (AGPL-3.0, programa externo): el más completo para partituras impresas, PDF vectoriales y
  escaneos. Da posiciones y puntuación por nota (proyecto `.omr`). Es el motor principal automático.
- **HOMR 0.7** (AGPL-3.0): transformers entrenados también con fotos de móvil. Motor principal para
  fotografías y segundo motor de verificación o respaldo.
- **oemer** (MIT): opcional, instalación aparte (ver §6).

La selección automática usa Audiveris salvo que la mayoría de las páginas sean fotos. El segundo motor se
ejecuta solo si el primero falla en alguna página o si más del 25 % de los compases es incoherente (o
siempre, si lo eliges). Cuando hay dos resultados, los compases se **alinean**; si coinciden se marcan como
verificados y si no, se ofrece la versión del otro motor como corrección **que tú aceptas o rechazas**.

**Texto:** nunca se convierte en notas. Se clasifica por su posición respecto de los pentagramas, su tamaño
y su contenido en: título, subtítulo, compositor, créditos, instrumento, número de página, número de
compás, referencia editorial, nota del editor, letra, cifrado, tempo, cambio de tempo, carácter, dinámica,
marca de ensayo, navegación (D.C., D.S., Coda, Fine), 8va, pedal, digitación, compases de espera,
o **dudoso**. Solo tienen efecto musical: tempo, cambios de tempo con destino conocido, dinámicas,
navegación, 8va (aplicada si la línea se mide con claridad, propuesta si no), pedal y compases de espera (aviso). Un número suelto solo se
toma como metrónomo si viene de la capa de texto de un PDF vectorial (con OCR hace falta el signo «=»).

**Piano:** los dos pentagramas forman una sola interpretación sincronizada. Pentagrama superior = mano
derecha, inferior = izquierda, respetando los cruces de pentagrama por voces. Editable nota a nota.

**MIDI:** se genera desde el modelo validado (no desde el archivo del motor): tipo 1 con pista de tempo y una
pista por mano (o por parte), tipo 0 opcional, ligaduras de prolongación sin segundo ataque, silencios como
tiempo entre eventos, cambios de tempo, rit./accel. interpolados cuando se conoce el tempo de destino,
calderones (alargan), dinámicas y reguladores → intensidad, acentos y staccato, pedal → CC64, marcadores de
texto y letra. Si una palabra de tempo no tiene número se usa el **tempo predeterminado** (100, ajustable) y
se propone el valor típico de la palabra sin aplicarlo.

---

## 4. Opciones avanzadas

*Opciones avanzadas* (barra lateral): resolución (automática, 200–600 ppp), corrección de imagen
(automática, ninguna, suave, fuerte), motor (automático, Audiveris, HOMR, oemer), segundo motor (si hace
falta, siempre, nunca), detección de texto, cifrados a notas, dos manos, tiempo máximo por página, tempo
predeterminado, repeticiones (expandir o no), dinámicas, pedal, calderones, articulaciones, rango de notas
válido, carpeta de salida, rutas de los motores y tema claro/oscuro.

Si el resultado de una partitura no es bueno: *Analizar → Volver a analizar con otras opciones…* (no hay que
importarla de nuevo). Para fotos difíciles prueba «Corrección de imagen: fuerte» o «Motor: HOMR»; para
partituras muy densas, 400–600 ppp.

---

## 5. Proyectos, recuperación y archivos temporales

- *Guardar proyecto* (Ctrl+S) crea un `.ncsproj` con el origen, las imágenes originales y procesadas, los
  resultados de los motores, la partitura corregida, los avisos y tus decisiones, y las versiones de los
  motores. No depende de que el archivo original siga en su sitio.
- El `.ncsproj` es un archivo **comprimido** (ZIP), no un texto: si lo abres con el Bloc de notas verás miles
  de caracteres sin sentido (a menudo chinos, porque el Bloc de notas lo toma por texto UTF-16). Es normal y
  el archivo no está dañado. Se abre desde Note Cascade Studio (*Importar → Abrir proyecto* o Ctrl+O). Para
  compartir la música con otros programas usa el MusicXML o el MIDI.
- Copia automática cada 45 s mientras hay cambios. Si la aplicación se cierra de forma inesperada, al abrirla
  ofrece recuperar el trabajo.
- *Diagnóstico → Limpiar archivos temporales* borra las carpetas de trabajo de conversiones anteriores (nunca
  los proyectos guardados). Los resultados de los motores se reutilizan si vuelves a analizar exactamente las
  mismas páginas con la misma versión.

---

## 6. Motores opcionales: oemer

oemer no se instala por defecto (última versión de 2023; sus dependencias pueden chocar con las de HOMR y
su autor recomienda HOMR). Para probarlo, en una consola dentro de la carpeta del proyecto:

```
tools\uv\uv.exe python install 3.11 --no-bin
tools\uv\uv.exe venv --python 3.11 tools\oemer
tools\uv\uv.exe pip install --python tools\oemer\Scripts\python.exe oemer "numpy<2" "onnxruntime==1.16.3" "opencv-python<4.11" "opencv-python-headless<4.11"
```

Las versiones fijadas son necesarias: con ONNX Runtime ≥ 1.17 su modelo falla («pads must not contain
negative values») y ONNX Runtime 1.16 necesita NumPy 1.x. Como queda en su propio entorno, no afecta a la
aplicación. Se detecta automáticamente (`tools\oemer\Scripts\oemer.exe`) y aparece en *Diagnóstico*. La
primera ejecución descarga sus modelos (≈ 40 MB). Es lento: ≈ 5 minutos por página en CPU.

---

## 7. Pruebas

- `test_windows.bat` — pruebas del programa (≈ 15 s): importación, PDF de todos los tipos, imágenes con
  transparencia, preprocesado, pentagramas, MusicXML (contrastado con music21), MIDI (releído), repeticiones,
  validación, correcciones, textos, proyecto, reproductor e interfaz.
- `test_windows.bat completo` — además, pruebas con los motores reales y la medición de precisión con
  partituras de referencia (varios minutos). Resultados reales en `docs/RESULTADOS_PRUEBAS.md`.

Línea de órdenes:

```
.venv\Scripts\python.exe -m note_cascade --diagnostico
.venv\Scripts\python.exe -m note_cascade convertir "partitura.pdf" --salida "C:\Salida" --paginas 1-3
```

---

## 8. Problemas frecuentes

| Problema | Qué hacer |
|---|---|
| «No se encontró notación musical» | La imagen es pequeña o borrosa, o no tiene pentagramas. Prueba a 400–600 ppp o «Corrección: fuerte». |
| Una captura PNG sale vacía en otros programas | Algunas capturas guardan la tinta solo en el canal alfa. Note Cascade la compone sobre blanco automáticamente. |
| Muchos avisos «faltan/sobran tiempos» | El motor leyó mal figuras o voces. Escucha el compás, acepta la versión del otro motor si se ofrece o corrige las duraciones. |
| No reconoce el compás (p. ej. 12/8) | Se propone el compás deducido de la duración de los compases; acéptalo en la lista de avisos. |
| Notas una octava desplazadas en un pasaje con 8va | La 8va se mide en la imagen y se aplica solo a su pentagrama (aviso «Indicación 8va aplicada», con corrección para deshacerla). Para ver por qué una nota suena a una altura: selecciónala y pulsa *Nota → Diagnóstico de altura*; *Diagnóstico → Diagnóstico de alturas…* revisa toda la partitura y exporta un registro CSV. |
| No suena nada | Revisa *Diagnóstico → Salida de audio*; prueba «Sonido: Sintetizador MIDI de Windows». |
| Audiveris no aparece | Ejecuta de nuevo `install_windows.bat` o indica la ruta en *Opciones → Motores*. |

Registros: `%LOCALAPPDATA%\NoteCascadeStudio\registros`. Para obtener un informe del sistema en un
archivo (`diagnostico.txt`), ejecuta **`diagnostico_windows.bat`**. La configuración se guarda en
`%LOCALAPPDATA%\NoteCascadeStudio\configuracion.json` (ejemplo comentado en `config\configuracion_ejemplo.json`).

Lee también **`LIMITACIONES.md`** (qué notación se admite y qué no) y **`LICENCIAS_TERCEROS.md`**.
La lista completa de símbolos que reconoce el programa, su efecto y el orden en que lee una partitura está en
**`docs/Simbolos_y_orden_de_lectura.pdf`** (se regenera con `python scripts/make_symbols_pdf.py`).
