# Plan de desarrollo - Audio Handler PV

## Objetivo
Construir un programa local para mezclar microfono, musica en loop y efectos de sonido en una salida unica que otros programas, como Discord, puedan usar como entrada.

## Supuestos iniciales
- Todo el contenido debe poder funcionar sin depender de servicios en internet.
- La musica y los efectos se trabajaran con archivos locales, principalmente mp3.
- La salida final debe comportarse como un dispositivo virtual o una ruta equivalente que otros programas puedan seleccionar.
- La interfaz visual debe permitir administrar bibliotecas de musica y efectos por categoria.

## Estructura propuesta
- `data/music/`: musica de fondo en loop.
- `data/sfx/`: efectos de sonido disparables por evento.
- `data/recordings/`: grabaciones o pruebas de salida.
- `data/config/`: configuracion local, presets y mapeos.
- `main.py`: arranque de la aplicacion y panel principal.

## Fases de desarrollo
### Fase 1 - Base del proyecto
- Definir la estructura de carpetas local.
- Crear la interfaz inicial.
- Cargar y listar archivos mp3 locales.
- Preparar atajos de teclado y estados visuales.

### Fase 2 - Motor de audio
- Evaluar la libreria de audio local mas adecuada.
- Construir un mezclador por canales para microfono, musica y efectos.
- Mantener la musica en loop continuo.
- Lanzar efectos de sonido sin silenciar otras fuentes.

### Fase 3 - Salida unica
- Enrutar la mezcla a un unico dispositivo de salida.
- Preparar una salida virtual seleccionable por otras aplicaciones.
- Verificar compatibilidad con Discord y programas similares.

### Fase 4 - Gestion visual
- Agregar importacion de archivos por categoria.
- Permitir listas, seleccion, activacion y desactivacion.
- Incluir controles de reproduccion y volumen por canal.
- Mejorar la vista con paneles separados para musica, efectos y microfono.

### Fase 5 - Atajos y automatizacion
- Definir atajos globales o de ventana para acciones rapidas.
- Lanzar efectos desde el teclado.
- Cambiar musica de fondo sin interrumpir la mezcla.
- Activar y desactivar microfono de forma inmediata.

### Fase 6 - Pruebas y ajuste fino
- Medir latencia de entrada y salida.
- Verificar estabilidad con varias pistas y efectos simultaneos.
- Ajustar niveles de audio para evitar saturacion o clipping.
- Probar el flujo completo con llamadas de prueba.

## Siguiente paso tecnico recomendado
Elegir la libreria de audio y la estrategia de dispositivo virtual antes de implementar la mezcla real, porque esa decision define el resto del motor.