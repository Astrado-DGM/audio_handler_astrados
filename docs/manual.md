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

## Música y efectos

- **Agregar música / Agregar efecto** copia un MP3 a tu biblioteca.
- Cada pista tiene botones de **reproducir**, **pausar** y **detener**.
- En la pestaña **Música**, arrastra la barra de progreso para saltar a otra parte de la canción. La pista activa se marca con un borde morado.
- **Loop música / Loop SFX** (en Config) hace que se repitan.
- Usa el **Filtro** para buscar por nombre.

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
- Play SFX: `Ctrl+Alt+Shift+E`
- Stop SFX: `Ctrl+Alt+Shift+W`
- Pausa SFX: `Ctrl+Alt+Shift+Q`
- Guardar ajustes: `Ctrl+Alt+Shift+S`

Si otra app ya usa una combinación, la app te avisa y puedes elegir otra.

## Escucharte (monitor)

**Escucharme localmente** duplica la música y los SFX en la **Salida monitor** (por ejemplo, tus audífonos), para que escuches lo mismo que tus amigos.

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
