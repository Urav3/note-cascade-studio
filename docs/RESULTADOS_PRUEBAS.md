# Resultados reales de las pruebas

Equipo de prueba: Windows 11 Pro, AMD Ryzen 7 5800X, sin GPU usada (CPU). Python 3.12.15, PySide6 6.12,
Audiveris 5.11.0, HOMR 0.7.0. Fecha: 24/10/2026. Todo lo que aparece aquí se ejecutó realmente.

## 1. Pruebas del programa (`test_windows.bat`, `pytest -m "not omr"`)

**153 pruebas superadas, 0 fallidas** (≈ 15–50 s según la carga del equipo). Con el repositorio recién
descargado de GitHub (sin `test_samples/`, `fotos/` ni `tools/`) pasan las mismas 153; de las 19 con motores
reales, 6 pasan y 13 se saltan solas por falta de Audiveris o de las partituras.

| Área | Qué se comprueba |
|---|---|
| Importación | Detección por contenido (extensión engañosa), archivo vacío, formato no admitido, PNG dañado, PDF dañado, PDF con contraseña, PDF vectorial / con imagen / mixto, capa de texto con posiciones, página PDF girada, PDF de varias páginas, TIFF multipágina, PNG con la tinta solo en el canal alfa, 16 bits, CMYK, orientación EXIF, documento del portapapeles, los 4 archivos de `fotos\` se abren |
| Imagen | Pentagramas, sistemas (1 y 2 pentagramas) y barras en páginas sintéticas; página sin música; imagen limpia sin modificar; inclinación ±1,5°/−2,5° corregida; página al revés y girada 90°; foto con perspectiva y sombra; ampliación de imágenes pequeñas; ida y vuelta de coordenadas por la matriz |
| Música | Utilidades de altura; MusicXML exportado contrastado con **music21** (mismos eventos) y releído; MXL comprimido; tresillos, ligaduras (un solo ataque), acordes, pedal, calderón, dinámicas; MIDI tipo 0 y 1 con las mismas notas; MIDI reimportado con las mismas alturas y duraciones; silencios como tiempo; repeticiones, casillas, D.C. al Fine, D.S. sin segno; accelerando interpolado; cambio de compás (MusicXML y MIDI); **ligadura que continúa entre páginas**; manos con cruce de pentagrama |
| Validación | Compás con tiempos de más (error comprobado) y de menos; anacrusa no penalizada; compás no reconocido con propuesta aplicable; error de octava sospechado pero no en octavas alternas; alteración sin justificar con corrección; reinterpretación al cambiar armadura y clave; duplicados, ligaduras sueltas, tresillos incompletos; armaduras distintas entre manos; comparación entre motores y sustitución de compás; deshacer/rehacer; añadir, mover, cambiar duración, borrar |
| Texto | Clasificación contextual (título, compositor, tempo con y sin número, metrónomo, número de compás, instrumento, letra, rit., dinámica, cifrado, D.C. al Fine, referencia editorial, número de página); los números de OCR sin «=» nunca son tempo; **ningún texto genera notas**; tempo tomado del texto con propuesta; títulos ilegibles descartados, legibles conservados; el signo «Ped.» adornado que el OCR lee como «Reo.»/«Leo.» es pedal y no letra |
| Proyecto y exportación | Guardar/abrir `.ncsproj`; exportar MIDI 1, MIDI 0, MusicXML, informe y proyecto (todo verificado); exportar una partitura vacía da error claro |
| Reproductor | Sintetizador con pistas por mano, cambio de velocidad, reloj silencioso, búsqueda y bucle; pausar mientras se genera el sonido no lo arranca solo; tempos desordenados y calderones no dejan el tempo cambiado; unísono entre manos suena una vez y se avisa |
| Alturas y octavas (`test_alturas_octavas.py`) | Altura escrita desde clave y posición (sol, fa, varias líneas adicionales arriba y abajo, cambio de clave), acorde, secuencia grave ascendente; rótulos 8va/8vb/8va bassa/15ma/15mb; 8va, 8vb y 15ma aplicadas una sola vez, solo a su pentagrama y solo en su tramo (termina a mitad de pieza); línea que cruza de página; línea sin rótulo que no continúa nada se ignora; alturas que el motor ya dio sonoras no se transponen otra vez; marca del motor sin fin ni prueba en la imagen no se aplica; MIDI exportado = reproductor = modelo y teclas de la cascada en orden, sin plegar octavas fuera del rango; MusicXML de ida y vuelta conserva la altura sonora; tecla sostenida que la otra mano vuelve a tocar: se suelta antes y el MIDI nunca tiene la misma tecla pulsada dos veces (en «The Pool», 23 notas; c. 64) |
| Posiciones y figuras en la imagen (`test_posiciones_imagen.py`) | La x de una nota sin posición sale de las notas del mismo instante; una cabeza algo peor dibujada en el instante y la altura de la nota se usa en vez de proponer quitarla; con una cabeza sin explicar en ese instante se avisa de la altura (no se propone quitar); sin cabeza sí se propone, nunca se quita sola; un ♮ leído como nota se quita y se puede reponer; dos cabezas reales en el mismo sitio no se tocan; armadura de cortesía y columna de Audiveris sin notas no son compases; figura del metrónomo (negra, blanca, corchea, con y sin puntillo) leída de la imagen; cambiar un tempo cambia también la figura escrita en el MusicXML; aviso de re-articulación con su propio título |
| Motivos y arpegios (`test_motivos_arpegio.py`) | Acorde arpegiado: de grave a agudo (≈ 45 ms, menos del 40 % de una figura corta), cada nota hasta el final del acorde, cada mano su acorde, la continuación de una ligadura no se vuelve a tocar, se desactiva con las articulaciones, se pone/quita en el acorde entero, se conserva en MusicXML y se copia del segundo motor; notas añadidas por el segundo motor y vistas en la imagen no se marcan «revisar»; «8va» con rótulo y línea medidos es información; cada «revisar» con su motivo (aviso, poca confianza o compás); bajar/subir un semitono respeta la armadura (Mi mayor: Mi → Re♯) y un cambio de octava conserva la alteración escrita |
| Vista previa de correcciones | La vista previa muestra lo que cambia (antes → después) sin modificar la partitura; en la revisión el editor dibuja el cambio y se puede ocultar |
| Ritmo, ligaduras y calderones | Varias figuras demasiado largas en una voz detectadas por su espaciado; calderón falso quitado (y restaurable) solo si el otro motor ve los demás |
| Selección y comparación | Ctrl+clic cerca de una nota la añade sin cambiar de compás; Mayús+arrastrar selecciona un área; la versión del otro motor no se ofrece si perdería notas confirmadas en la imagen y no se acepta en bloque |
| Repeticiones y escucha | Casillas sin barra de repetición (líneas 8va confundidas) no hacen saltar compases; tras una repetición los compases siguientes son 1.ª vez; manos separadas solo en los pasajes donde se juntan y solo al escuchar (en «The Pool»: c. 16, 25, 32–37, 40–44, 48 y 61–68); «Aceptar todas las de este tipo» no mezcla aplicar con deshacer |
| Aprendizaje y reglas generales | Corrección del usuario propuesta en los compases que el motor leyó igual; preferencias aprendidas entre partituras (3 aceptaciones, un rechazo la desactiva, aplicar ≠ deshacer); puntillo perdido; 3/8 escondido en 12/8; figura demasiado larga detectada por su espaciado |
| Reparaciones entre motores | El segundo motor completa notas de un acorde que el principal perdió; no se mezcla si contradice una nota del principal; acorde ligado conserva ♮/♭ tras la barra (y sin ligadura se respeta la armadura); silencio de compás completo en 12/8; melodía sobre acorde largo separada en otra voz; cada nota de un unísono conserva su mano |
| Octavas y tempo del texto | «8va» medida en la imagen y aplicada en varios sistemas, y deshecha por su corrección; sin medida solo se propone; no se aplica dos veces si el motor ya la tuvo; el tempo se coloca en el sistema asignado por el clasificador |
| Interfaz | Los cinco pasos; edición desde el rollo de piano con deshacer/rehacer y añadir nota; aceptar una corrección propuesta; el panel «Nota» dice por qué una nota está marcada y abre su aviso, varias notas sin valores sobrantes, arpegiar un acorde y deshacerlo; visualizador en cascada; ventana «Ver partitura» resalta las notas que suenan; Ctrl+V de captura con transparencia; «Licencias y ayuda» enlaza al código fuente y a los avisos; «Informar de un problema» copia el diagnóstico sin la carpeta de usuario y solo abre GitHub si se acepta |

## 2. Pruebas con motores reales (`pytest -m omr`)

**Alturas con PDF reales (`test_samples/`):**

- *Sinner's Finale*: 8va de la mano derecha aplicada en c. 16–30, 35–46 (cruza de la página 2 a la 3) y 55–56;
  8va de la mano izquierda en c. 58–59 y 61; cambio a clave de sol de la izquierda en el c. 58 (Audiveris no lo
  vio) y vuelta a fa en el 59; «8vb» inventada por el motor en el c. 31 no aplicada. c. 37: Do6 Do7 La6 La7
  arriba y Fa1 (4 líneas adicionales) abajo; c. 58 izquierda La6 Si♭6 Re7. Diagnóstico: 675 de 676 notas
  coinciden con la imagen (la otra no tiene posición exacta). Antes de la corrección la mano derecha sonaba
  una octava por debajo en todos los pasajes con 8va (Audiveris entrega alturas escritas y el programa las
  tomaba como sonoras).
- *The Pool* c. 38–41: La1/La2 y arpegio La2 Mi3 La3 Do♯4 Mi4; acordes Sol♯5 Do♯6 Sol♯6; sin desplazamientos.
- *The Pool* c. 44: antes quedaba con posiciones aproximadas (la armadura de cortesía tras la doble barra se
  contaba como otro compás) y se proponía quitar dos Sol♯3 que están en la imagen; ahora los 69 compases tienen
  posición exacta (1398 de 1403 notas localizadas, antes 1362) y las dos notas quedan confirmadas. c. 24: el ♮
  de Do5 que Audiveris leía como otra nota (Do♯5) se quita con aviso. Ligaduras 41→42 (tres, cruzan de
  página) y las siete indicaciones «♩. = …» leídas de la imagen.
- *The Pool* / *In The Pool* c. 14: acorde arpegiado de redondas con puntillo (Sol♯3 Si3 | Re♯4 Mi4 Sol♯4) que
  Audiveris no ve y HOMR sí; las cinco notas se comprueban en la imagen y ya no salen «revisar», y se tocan de
  abajo arriba. La «8va» de los c. 17–21 (rótulo y línea medidos) ya no marca sus 46 notas. En *In The Pool*,
  notas marcadas «revisar»: 112 → 67, todas con su motivo en el panel «Nota» (unísono entre manos, figuras
  acortadas por su espaciado, ligaduras y puntillos corregidos con la imagen…).


**19 pruebas superadas** (≈ 12 min), con partituras de referencia generadas para las pruebas (piano a dos
manos, 8 compases, 4/4, Sol mayor, acordes, puntillos, silencios, alteraciones, título, compositor, letra,
dinámicas) y con los archivos del usuario:

- Precisión mínima exigida (altura) ≥ 85 % y como máximo 2 notas adicionales, para Audiveris y HOMR, en:
  PNG, PDF vectorial, PDF con imagen escaneada, foto simulada (perspectiva, sombra, JPEG 60 %), PDF girado 90°.
- Verificación cruzada con los dos motores: ≥ 6 de 8 compases quedan verificados por acuerdo.
- Página sin música → error claro «No se encontró notación musical».
- `mi foto.png` → 17 compases, ≥ 230 notas, piano con las dos manos, **ninguna nota sobre la letra**, exportación verificada.
- `The Pool.pdf` (8 páginas) → 69 compases, ≥ 1200 notas, páginas en orden, duración razonable (sin páginas duplicadas), tempo recuperado del texto.
- Flujo completo en la interfaz real: importar → analizar en segundo plano → aceptar corrección → exportar → archivos verificados.

## 3. Precisión medida con partituras de referencia (`tests\omr_accuracy.py`)

Métrica por compás alineado: *precisión* = notas reconocidas correctas / reconocidas; *exhaustividad* =
correctas / de referencia; *exactas* = altura + inicio + duración correctos.

| Caso | Motor | Compases | Notas ref./rec. | Precisión altura | Exhaustividad | Exactas (altura+ritmo) | Omitidas | Adicionales | Tiempo |
|---|---|---|---|---|---|---|---|---|---|
| Imagen PNG (piano, con letra y títulos) | Audiveris | 8/8 | 54/54 | 100 % | 100 % | 100 % | 0 | 0 | 14.9 s |
| Imagen PNG (piano, con letra y títulos) | HOMR | 8/8 | 54/54 | 100 % | 100 % | 96 % | 0 | 0 | 12.5 s |
| PDF vectorial (piano) | Audiveris | 8/8 | 54/54 | 100 % | 100 % | 100 % | 0 | 0 | 11.7 s |
| PDF vectorial (piano) | HOMR | 8/8 | 54/54 | 100 % | 100 % | 96 % | 0 | 0 | 11.0 s |
| PDF con imagen escaneada (piano) | Audiveris | 8/8 | 54/54 | 100 % | 100 % | 100 % | 0 | 0 | 16.1 s |
| PDF con imagen escaneada (piano) | HOMR | 8/8 | 54/54 | 100 % | 100 % | 96 % | 0 | 0 | 12.9 s |
| Foto simulada JPG (perspectiva, sombra) | Audiveris | 8/8 | 54/54 | 100 % | 100 % | 100 % | 0 | 0 | 17.7 s |
| Foto simulada JPG (perspectiva, sombra) | HOMR | 8/8 | 54/54 | 100 % | 100 % | 96 % | 0 | 0 | 14.6 s |
| PDF con página girada 90° | Audiveris | 8/8 | 54/54 | 100 % | 100 % | 100 % | 0 | 0 | 15.6 s |
| PDF con página girada 90° | HOMR | 8/8 | 54/54 | 100 % | 100 % | 96 % | 0 | 0 | 14.1 s |
| Captura pequeña (900 px de ancho) | Audiveris | 8/8 | 54/52 | 100 % | 96 % | 96 % | 2 | 0 | 14.1 s |
| Captura pequeña (900 px de ancho) | HOMR | 8/8 | 54/54 | 100 % | 100 % | 96 % | 0 | 0 | 11.8 s |
| Melodía en un pentagrama (3/4, bemoles) | Audiveris | 4/4 | 9/9 | 100 % | 100 % | 100 % | 0 | 0 | 10.7 s |
| Melodía en un pentagrama (3/4, bemoles) | HOMR | 4/4 | 9/9 | 100 % | 100 % | 100 % | 0 | 0 | 10.2 s |

**Interpretación honesta:** estas partituras están grabadas por ordenador (Verovio) y son fáciles; miden
que el flujo completo (preparación, motor, lectura, integración) no introduce errores y que el texto no se
convierte en notas, **no** la precisión en partituras reales difíciles. En HOMR, el 4 % de notas «no
exactas» corresponde al ritmo de una figura en el compás con silencio.

## 4. Archivos reales del usuario (`fotos\`, línea de órdenes)

No hay MusicXML de referencia para estos archivos, así que no se puede medir su precisión exacta; se
informa de lo que se obtuvo y de los avisos generados.

| Archivo | Tipo | Motor | Resultado | Tiempo | Observaciones |
|---|---|---|---|---|---|
| mi foto.png | captura PNG con transparencia | Audiveris | 17 compases, 275 notas/silencios, posiciones exactas de todas | 30 s | 1 error probable y 5 avisos; la letra «side by side with me…» se clasifica como letra y no genera notas |
| The Pool.pdf | PDF vectorial, 8 páginas | Audiveris | 69 compases, 1333 notas; posiciones exactas en 68 de 69 compases | ≈ 100 s | 12/8 no leído → propuesto; tempos ♩.=65 (c. 1, 29, 52), 75 (c. 5 y 39), 133 (c. 22), 45 (c. 69) y «accel.»/«rit.» recuperados; tras los calderones el tempo vuelve al indicado; HOMR completa 14 notas que Audiveris no vio (redondas de los c. 1, 7, 8, 14, 56, 57) y aporta el 12/8; silencios de compás completo y voces de los c. 1 y 3 reparados; el acorde ligado ♮/♭ del final conserva sus alteraciones (errores probables 14 → 6); con la comprobación con la imagen y las reglas generales: 1265 notas confirmadas en la imagen, 39 notas situadas (todo el c. 44), 12/8 con el 3/8 del c. 60, puntillos y ritmos de los c. 17–20 y 58 corregidos (errores probables 6 → 1, compases sin dudas 31 → 41); las líneas 8va de los c. 17–21 que Audiveris tomó por casillas hacían saltar esos compases al reproducir: descartadas; becuadro del c. 24 aplicado desde la imagen; comparación completa con el PDF (resultado vuelto a dibujar como partitura): única 8va en c. 17–21 bien aplicada; corregidos el calderón falso del c. 7, las dos corcheas leídas como negras del c. 16 y la ligadura 41→42 que cruza de página; 0 errores probables; «8va» de los c. 17–21 (continúa en el sistema siguiente) medida y aplicada: los choques de manos de esos compases desaparecen (14 → 9 avisos; los 9 restantes son unísonos escritos, p. ej. Mi4 en las dos manos en el c. 40) |
| beethoven.pdf (págs. 1–3) | escaneo de 2004 | Audiveris | 69 compases, 1235 notas, 1271 de 1297 figuras localizadas | ≈ 85 s | 16 errores rítmicos señalados en compases densos; digitaciones y números de compás en círculo no se usan como tempo |
| claro de luna.pdf (págs. 1–2) | parte de oboe, franjas de imagen | Audiveris + HOMR | 84 compases, 433 notas | ≈ 100 s | Muchos compases incoherentes → se ejecutó HOMR automáticamente; los motores coinciden solo en el 11 % (imagen difícil, silencios de varios compases) y se avisa con un único mensaje general |
| mi foto.png | — | oemer (entorno aparte) | 17 compases, 263 notas | ≈ 5 min | Funciona tras fijar sus dependencias (ver README §6) |

## 5. Instalación desde cero y ejecutable

- **Instalación limpia:** se copió el proyecto sin `tools\` ni `.venv\` a otra carpeta y se ejecutó
  `scripts\install_windows.ps1` (lo mismo que hace `install_windows.bat`). Descargó y verificó uv, Python
  3.12, las dependencias, los modelos de HOMR, Audiveris y el OCR. La prueba encontró dos fallos reales que
  se corrigieron: (1) `msiexec` no puede extraer Audiveris en rutas de más de 260 caracteres (ahora se extrae
  en una carpeta temporal corta y se mueve con robocopy); (2) un resumen SHA-256 mal copiado en el script, que
  la propia verificación detectó (ahora las descargas de tessdata están fijadas a un commit concreto). Tras
  las correcciones: diagnóstico «todo lo esencial está disponible» y conversión de `mi foto.png` correcta
  (17 compases, 275 figuras, MIDI y MusicXML verificados).
- **Ejecutable (`build_windows.bat`):** carpeta `dist\NoteCascadeStudio` de ≈ 930 MB con Audiveris, OCR,
  modelos de HOMR y el código fuente (`codigo_fuente.zip`). Probado con `NoteCascadeStudio-consola.exe`:
  diagnóstico completo correcto y conversión de `mi foto.png` con HOMR (17 compases, 15 s, HOMR lanzado
  desde el propio ejecutable) y con Audiveris (17 compases, 20 s, todas las posiciones).
  Primer intento: PyMuPDF no quedaba completo en el paquete; se corrigió en `NoteCascadeStudio.spec`.

## 6. Otras comprobaciones

- Vídeo MP4 del visualizador: 960×540, 24 fps, H.264 + AAC, generado en 6,5 s para 11,5 s de música.
- Reproducción real por la tarjeta de sonido: el reloj avanza 1,37 s en 1,5 s (compensación de latencia) y
  respeta búsquedas y cambios de velocidad.
- Exportación de MIDI de los cuatro archivos: todos se releyeron con el mismo número de notas esperado.
- **Recorte de márgenes:** al probarlo, Audiveris reconoció peor la captura recortada (265 notas en lugar de
  275, pérdidas en los compases 10 y 14). Por eso el recorte solo se aplica con «Corrección de imagen:
  fuerte», elegida a mano.
- **oemer:** con las versiones actuales de ONNX Runtime su modelo no carga; con `numpy<2`,
  `onnxruntime==1.16.3` y OpenCV 4.10 en su entorno aislado funciona (17 compases, ≈ 5 min por página).

## 7. Lo que no se pudo probar

- Uso interactivo con ratón por una persona: la interfaz se probó renderizándola fuera de pantalla y con
  pruebas automáticas (incluido el flujo completo importar → analizar → corregir → exportar); conviene
  probar los arrastres en el editor con tu ratón.
- Windows 10 (solo se probó en Windows 11) y otro equipo distinto de este.
- Partituras fotografiadas de verdad con el móvil (solo fotos simuladas): no había ninguna entre los archivos.
