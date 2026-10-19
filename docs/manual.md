# Manual de uso

## Qué hace

Astrados Audio Handler junta en una sola señal tu **micrófono**, **música de fondo** y **efectos de sonido (SFX)**, y la envía a Discord (o a cualquier programa de voz) como si fuera un micrófono.

El audio viaja así:

- Música y SFX → salida objetivo (**CABLE Input**)
- Micrófono → filtro de la app (pasa-altos, supresión de ruido, puerta) → **CABLE Input**
- Discord escucha **CABLE Output** como su micrófono
- Los sonidos de la propia interfaz suenan por tus altavoces, nunca por el cable

## Primer uso

1. **Instala VB-Cable.** Windows no permite que un programa normal cree un micrófono, por eso se usa este driver gratuito. Si no está instalado, la app te muestra una guía: descarga el ZIP desde vb-audio.com/Cable, ejecuta VBCABLE_Setup_x64.exe como administrador, pulsa "Install Driver" y **reinicia Windows**.
2. Abre la app y pulsa **Config > Buscar de nuevo**. Si se detecta el cable, se selecciona solo como salida objetivo.
3. En **Config > Dispositivos**, elige tu micrófono real en **Entrada micrófono**.
4. Activa **Micrófono activo** (o usa el botón **Micrófono** de la barra superior).
5. En **Discord > Ajustes de usuario > Voz y vídeo**:
   - Dispositivo de entrada: **CABLE Output (VB-Audio Virtual Cable)**
   - Desactiva **Supresión de ruido**, **Cancelación de eco** y **Control automático de ganancia**. El filtro de esta app ya limpia tu voz, y los de Discord cortan la música.
   - En **Sensibilidad de entrada**, baja el umbral para que la música no se corte cuando no hablas, o usa pulsar para hablar.

## Biblioteca y carpetas

- **Agregar música / Agregar efecto** copia uno o varios archivos (MP3, WAV, OGG o FLAC) a tu biblioteca, dentro de la carpeta elegida.
- Organiza la música y los efectos en **carpetas**: con **Nueva carpeta** creas una (por ejemplo "Tranquila", "Batalla" o "Risas") y con el selector **Carpeta** eliges cuál ver. "Todas" muestra todo; "General" muestra solo los archivos sueltos.
- **Abrir carpeta** abre la carpeta elegida en el Explorador de Windows, por si quieres ordenar archivos a mano. Después pulsa **Actualizar biblioteca**.
- El botón rojo con papelera de cada fila envía ese archivo a la **Papelera de reciclaje**. Si te equivocas, lo puedes recuperar desde ahí.
- Usa el **Filtro** para buscar por nombre.
- Al pasar el mouse sobre los botones redondos aparece qué hace cada uno.

## Música

- Cada pista tiene botones de **reproducir**, **pausar** y **detener**. La pista que suena se marca con un borde morado.
- Arrastra la barra de progreso para saltar a otra parte de la canción.
- **Al terminar** define qué pasa cuando acaba una pista:
  - **Lista en ciclo**: sigue con la próxima de la carpeta elegida y, al llegar al final, vuelve a empezar.
  - **Aleatorio**: sigue con otra pista al azar de la carpeta.
  - **Repetir pista**: repite la misma canción.
  - **Una vez**: se detiene.

## Efectos

- **Reproducir** (azul) toca el efecto **una vez**. Puedes pulsarlo varias veces: los efectos se superponen sin cortarse entre sí ni cortar la música.
- **Loop** (flechas circulares) lo deja sonando en bucle; el botón se vuelve morado mientras está activo. Púlsalo de nuevo para detener ese loop.
- **Detener** (morado) detiene solo ese efecto.
- **Asignar atajo** le da a cada efecto su propia combinación de teclas, que funciona aunque la ventana esté minimizada. En el mismo diálogo puedes **quitar el atajo**. Si la combinación ya la usa otro atajo, la app te avisa.

## Micrófono y filtro nativo

En **Config > Filtro de micrófono**:

- **Pasa-altos 80 Hz**: quita zumbidos, golpes en la mesa y ruido grave.
- **Supresión de ruido de fondo**: reduce el ventilador, el teclado suave y el siseo. Con **Intensidad** regulas cuánto atenúa; si la voz suena metálica, bájala.
- **Puerta de ruido**: silencia el micrófono mientras no hablas.
- **Umbral de voz**: nivel desde el que se considera que estás hablando. Mira el medidor (entrada → salida) en silencio y pon el umbral unos dB por encima del nivel de salida.

Los cambios del filtro se aplican al instante.

## Ducking (bajar la música al hablar)

Con **Bajar música y SFX al hablar** activo, la música y los efectos bajan solos mientras hablas y vuelven a subir suavemente al callarte. **Volumen al hablar** define cuánto bajan (por ejemplo, 40%). El medidor muestra "voz" o "silencio" y el porcentaje actual.

## Atajos globales

Funcionan aunque la ventana esté minimizada. Se editan en la pestaña **Atajos**: escribe la combinación o pulsa **Capturar**.

- Actualizar biblioteca: `Ctrl+Alt+Shift+R`
- Alternar micrófono: `Ctrl+Alt+Shift+M`
- Play música: `Ctrl+Alt+Shift+P`
- Stop música: `Ctrl+Alt+Shift+O`
- Pausa música: `Ctrl+Alt+Shift+I`
- Música siguiente: `Ctrl+Alt+Shift+N`
- Play SFX (el último efecto usado, una vez): `Ctrl+Alt+Shift+E`
- SFX siguiente (recorre la carpeta elegida): `Ctrl+Alt+Shift+D`
- Stop SFX (todos, incluidos los loops): `Ctrl+Alt+Shift+W`
- Pausa SFX: `Ctrl+Alt+Shift+Q`
- Guardar ajustes: `Ctrl+Alt+Shift+S`

Además, cada efecto puede tener su propio atajo (pestaña **Efectos > Asignar atajo**).

Si otra app ya usa una combinación, la app te avisa y puedes elegir otra.

## Escucharte (monitor)

**Escucharme localmente** duplica la música y los SFX en la **Salida monitor** (por ejemplo, tus audífonos), para que escuches lo mismo que tus amigos. Puedes cambiar la salida monitor mientras suena algo: la música sigue en el nuevo dispositivo y el anterior deja de sonar.

## Dónde se guardan tus datos

- Versión instalada: `%LOCALAPPDATA%\Astrados Audio Handler` (música, efectos, configuración y logs).
- Versión de desarrollo: carpeta `data` del proyecto.

Pulsa **Guardar ajustes** para que la configuración se mantenga al reiniciar.

## Solución de problemas

- **Discord no escucha nada**: revisa que la salida objetivo sea CABLE Input y que la entrada de Discord sea CABLE Output.
- **La música se corta en Discord**: desactiva la supresión de ruido y la sensibilidad automática de Discord.
- **Mi voz suena robótica**: baja la intensidad de supresión de ruido.
- **Se corta el final de mis frases**: baja el umbral de voz.
- **La música suena por mis altavoces y no en Discord**: la barra de estado avisa si la salida no se pudo abrir. Elige CABLE Input y pulsa **Aplicar ajustes**.
- **Un atajo no funciona**: otra app ya lo usa. Cámbialo en la pestaña Atajos.

## Log de errores

**Ayuda > Log de errores** muestra los errores y avisos recientes: dispositivos que no se pudieron abrir, cortes de audio, errores internos. Desde ahí puedes **copiarlo** para pedir ayuda, **abrir la carpeta** o **limpiarlo**.
