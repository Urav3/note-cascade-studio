# Limitaciones conocidas

Ningún sistema de reconocimiento óptico de música lee correctamente todas las partituras. Note Cascade
Studio intenta **detectar y explicar** los errores en lugar de ocultarlos, pero el resultado final depende
mucho de la calidad de la imagen y del motor. Esta lista es específica y honesta.

## Qué se reconoce bien (con partituras impresas claras)

- Pentagramas de 5 líneas, sistemas de uno o varios pentagramas, llaves/corchetes de piano.
- Claves de sol, fa y do; armaduras; alteraciones accidentales y su vigencia dentro del compás.
- Figuras de redonda a fusa, puntillos, silencios, acordes, varias voces por pentagrama (Audiveris).
- Ligaduras de prolongación (sin segundo ataque en el MIDI) frente a ligaduras de expresión (sin efecto).
- Tresillos y grupos irregulares habituales.
- Indicaciones de compás comunes y cambios de compás (con la excepción de abajo).
- Dinámicas, reguladores, acentos, staccato y pedal cuando el motor (Audiveris) los detecta.
- Barras de repetición y casillas de 1.ª/2.ª vez; D.C., D.S., Fine y Coda en los casos estándar.

## Soporte parcial

- **Compases poco habituales** (p. ej. 12/8 en «The Pool»): Audiveris a veces no los lee. La validación lo
  detecta y propone el compás deducido; la duración de las notas no se ve afectada.
- **Indicaciones metronómicas:** si el motor no las lee, se recuperan del texto del PDF vectorial. La figura
  de pulso (♩ o ♩.) suele ser un dibujo, no texto: se supone según el compás y se avisa para que lo
  confirmes. En imágenes, el OCR solo se usa si aparece el signo «=».
- **Palabras de tempo** (Allegro, Andante…) sin número: se usa el tempo predeterminado y se propone un valor
  típico; nunca se aplica un número inventado sin avisar.
- **rit./accel.:** se interpolan solo cuando el tempo de destino es conocido; si no, se mantiene el tempo y
  se informa.
- **8va / 8vb / 15ma / 15mb:** Audiveris 5.11 marca solo el *inicio* de cada línea de octava (nunca el
  final) y deja las notas con su altura *escrita*; además a veces no ve una línea o se inventa otra. Por eso
  el alcance se mide en la imagen (rótulo + línea discontinua, también cuando sigue en el sistema o la página
  siguiente) y se aplica solo al pentagrama que la lleva (8va: el de debajo; 8vb: el de encima). Antes de
  transponer se comprueba con la posición de las cabezas si las alturas del motor ya eran sonoras, para no
  aplicar nunca dos veces. Una marca del motor que la imagen no confirma no se aplica: queda como aviso.
  Limitación: una 8va escrita sin rótulo legible ni línea discontinua (p. ej. solo con «8» muy pequeño) puede
  pasar desapercibida si el motor tampoco la marca.
- **Cambios de clave dentro del pentagrama:** se comprueban en la imagen con las claves de sol y de fa
  aprendidas del principio de los pentagramas; si el motor no vio un cambio, se inserta y se recalculan las
  alturas desde la posición de cada cabeza. Las claves de do no se buscan dentro del pentagrama.
- **Diagnóstico de altura:** en la revisión, *Nota → Diagnóstico de altura* muestra el recorrido de la nota
  (clave, posición, líneas adicionales, alteración, octava, motor, modelo, MIDI exportado, reproductor y tecla
  de la cascada) y *Diagnóstico → Diagnóstico de alturas…* revisa toda la partitura y exporta un CSV. La
  altura esperada sale de la propia imagen; notas sin posición exacta en la imagen no se pueden comparar.
- **Tecla sostenida que se vuelve a tocar:** en un piano real no se puede volver a pulsar una tecla que sigue
  bajada. Si la partitura escribe una nota larga y, mientras suena, otra nota en la misma tecla (con la misma
  mano o con la otra), la larga se suelta justo antes del nuevo golpe al escuchar, en el MIDI y en la cascada;
  el pedal del sintetizador tampoco la prolonga por encima. La partitura y el MusicXML conservan lo escrito.
- **Misma tecla en las dos manos:** si las dos manos tocan la misma nota a la vez (unísono escrito o nota
  asignada a la mano equivocada) se ve en las dos manos pero suena una sola vez, y se avisa para revisarlo.
- **Redondas y acordes apilados:** Audiveris pierde a menudo redondas (sobre todo acordes de redondas con
  puntillo o con arpegio). Si HOMR está instalado se ejecuta y **completa** las notas que faltan, pero solo
  cuando confirma todas las demás notas de ese pentagrama y su ritmo es completo; si no, queda la propuesta de
  usar la versión del otro motor. Las notas añadidas sin una nota de referencia en el mismo pentagrama tienen
  posición aproximada y no se resaltan en «Ver partitura». Si todas las notas añadidas se encuentran luego en
  la imagen (cabeza a su altura y en su sitio), el aviso pasa a ser informativo y no se marcan «revisar».
- **Por qué una nota sale «revisar»:** el panel «Nota» lo dice siempre (el aviso que la incluye, con un enlace
  para abrirlo; la poca confianza del motor; o el aviso de su compás). Una «8va» con su rótulo y su línea
  medidos en la imagen se considera comprobada: es información y no marca sus notas. Si el inicio de la
  línea solo lo dio el motor, sí queda como aviso.
- **Comprobación con la imagen:** el programa aprende de cada partitura cómo son sus cabezas de nota y
  comprueba cada nota reconocida (cabeza en su sitio y a la altura correcta), sitúa las notas cuya posición
  era aproximada y añade cabezas que ningún motor vio cuando forman acorde con una nota conocida. Una cabeza
  suelta sin explicar solo se avisa. Las alteraciones (♯, ♭, ♮) también se aprenden de la partitura y se
  comprueban delante de cada nota; se necesitan al menos 3 ejemplos de cada una leídos por el motor. No se
  comprueban las alteraciones dobles ni la armadura.
- **Notas sin posición exacta:** su sitio se calcula con las demás notas localizadas del mismo compás (la
  columna de su mismo tiempo en cualquier pentagrama), no repartiendo el compás por igual. Si en ese sitio hay
  una cabeza a la altura de la nota se acepta aunque la coincidencia con el modelo sea menor (cabezas colgadas
  de una línea adicional o pegadas a un puntillo). Solo se propone quitar una nota cuando en su instante no hay
  ninguna cabeza sin explicar; si la hay, se avisa de que la altura o la figura pueden estar mal leídas.
- **Alteración leída como nota:** si el motor pone una nota sin cabeza justo delante de otra igual del mismo
  acorde que sí lleva alteración, se quita y se avisa (con «Volver a poner la nota quitada»).
- **Armaduras y compases «de cortesía»** al final de un sistema (antes del cambio del sistema siguiente) no se
  cuentan como compases, ni en la imagen ni en el proyecto de Audiveris.
- **Figura del metrónomo:** cuando el PDF dibuja la figura («♩.») y solo el número es texto, se lee en la
  imagen (negra, blanca o corchea, con o sin puntillo). Si no se distingue con claridad se supone la habitual
  del compás y se avisa. No se leen figuras con doble puntillo poco claras ni equivalencias («♩ = ♪»).
- **Versión del otro motor:** «Usar la versión de HOMR» solo se ofrece si no perdería notas confirmadas en
  la imagen (HOMR a veces no ve una mano entera o no aplica una 8va) y nunca se puede aceptar en bloque.
- **Ligaduras y calderones:** las ligaduras que el motor no ve se buscan en la imagen (arco entre dos notas
  iguales seguidas, o hasta el final del renglón cuando cruzan de sistema o de página). Un calderón que solo
  ve el motor principal se quita si HOMR coincide con él en los demás (si no, se deja).
- **Ritmo por el espaciado:** si una voz se sale del compás, se buscan las figuras que ocupan en la imagen el
  espacio de figuras más cortas; solo se corrige si hay una única explicación.
- **Casillas de 1.ª/2.ª vez:** solo se respetan si la 1.ª casilla termina en una barra de repetición; si no,
  se descartan (suelen ser líneas «8va» confundidas con casillas).
- **Aprendizaje:** dentro de una partitura, al corregir un compás se propone la misma corrección en los
  compases que el motor leyó igual, y «Aceptar todas las de este tipo» aplica de una vez una corrección en
  todos los avisos iguales (se deshace con un Ctrl+Z). Nada se aplica solo entre partituras: una versión
  anterior lo hacía y, al haber aceptado varias veces «tempo como negra», hacía sonar la pieza un 50 % más
  lenta. Los motores de reconocimiento no se reentrenan.
- **Reparaciones automáticas** (siempre con aviso): figura leída más larga de lo que indica su espacio en la
  imagen; puntillo perdido; compás de otra medida que el motor no leyó (p. ej. 3/8 dentro de un 12/8); silencio de compás completo leído como de 4 tiempos;
  melodía puesta «después» de un acorde largo en la misma voz (se separa en otra voz); acorde ligado sobre la
  barra que perdió sus alteraciones en el compás siguiente; indicación de compás leída solo por HOMR.
- **Calderones:** se alargan a la mitad del tempo durante la nota; no es una interpretación expresiva real.
- **Notas de adorno:** se tocan brevemente antes del tiempo; trinos, mordentes y trémolos se conservan en
  MusicXML pero no se desarrollan en el MIDI.
- **Arpegios (línea ondulada delante del acorde):** al escuchar, en el MIDI y en la cascada el acorde se toca
  de la nota más grave a la más aguda, muy seguidas (≈ 45 ms entre una y otra, menos si la figura es corta), y
  cada nota se mantiene hasta el final del acorde. Si cada mano tiene su línea, cada una arpegia su acorde a la
  vez. El signo lo dan los motores (si solo lo ve HOMR, se copia al acorde de Audiveris con una nota
  informativa); el programa no lo busca por su cuenta en la imagen. Si falta o sobra, se pone o se quita con
  **Arpegiar acorde / Quitar arpegio** en el panel «Nota». Se desactiva con «Aplicar articulaciones».
- **Glissandos, pedal «una corda»:** se conservan si el motor los da, sin efecto sonoro.
- **Cifrados de acordes** (C, Am7…): se clasifican como texto; solo se convierten en notas si activas la
  opción, y en una pista aparte.
- **Compases de espera** (silencios de varios compases): se detectan en la imagen y se avisa para comprobar
  que el número de compases coincide.
- **Asignación de manos:** por pentagrama y voz; en pasajes con manos cruzadas o un solo pentagrama hay que
  revisarla (es editable nota a nota).
- **Posiciones en la imagen:** exactas con Audiveris (proyecto `.omr`); con HOMR son **aproximadas** (se
  dibujan con borde punteado) porque esa versión no informa de dónde está cada nota.
- **Confianza:** solo Audiveris da puntuación por nota. Con HOMR, el estado verde solo aparece si hay acuerdo
  con otro motor; si no, la nota queda «sin datos» (no se inventa).
- **Comparación entre motores:** cuando discrepan en casi todos los compases (imágenes difíciles) se muestra
  un único aviso general en lugar de cientos de avisos.
- **Fotografías:** se corrigen perspectiva (si se ve el borde de la hoja), sombras, inclinación y escala, pero
  las páginas curvadas, desenfocadas o muy oscuras dan resultados pobres con cualquier motor.
- **PDF vectoriales:** se rasterizan para el reconocimiento; su capa de texto se aprovecha para títulos,
  tempos y números de compás. Si el PDF dibuja los símbolos musicales como trazados (lo habitual, como en
  «The Pool»), no se pueden extraer las notas directamente como datos.

## No implementado

- Notación manuscrita, tablaturas de guitarra, diagramas de acordes, notación de percusión compleja,
  notación antigua o contemporánea no estándar.
- Partituras orquestales grandes: funcionan, pero la revisión y la separación por instrumento no están
  optimizadas (se exporta una pista por parte).
- Transposición de instrumentos transpositores: se respeta si el motor la indica en el MusicXML (`<transpose>`);
  no se deduce de la imagen.
- Polirritmias complejas y compases sin métrica (*senza misura*) solo en la medida en que el motor los entregue.
- Corrección automática de notas sin intervención del usuario: deliberadamente no se hace.
- SoundFonts (.sf2): no hay reproductor de SoundFont; se usan el sintetizador interno o el MIDI de Windows.
- La exportación de vídeo (MP4) es opcional y lenta (se dibuja cada fotograma).

## Rendimiento orientativo (Ryzen 7 5800X, CPU)

- Audiveris: ≈ 10–20 s por página (más el arranque de Java, ≈ 5 s).
- HOMR: ≈ 12–20 s por página.
- Preparación y texto: 1–4 s por página (el OCR es la parte más lenta en escaneos).

## Pruebas no realizadas

Ver `docs/RESULTADOS_PRUEBAS.md`: allí se indica qué se probó con motores reales y qué no (por ejemplo, la
instalación desde cero en un Windows limpio sin conexión previa no se pudo verificar en este entorno).
