# 00 - Empieza aquí

## ¿Qué es BlendTrack?

Una aplicación de escritorio hecha con **Python** y **PySide6 (Qt Widgets)** que
sirve para buscar, descargar, organizar y lanzar versiones de
[Blender](https://www.blender.org/): LTS, estables y de desarrollo (diarias y
ramas experimentales). Funciona en Linux, Windows y macOS, y está pensada para
ser **portable**: una vez empaquetada no hace falta instalar nada. También
descarga y lanza dos programas basados en Blender, **Bforartists** y **UPBGE**,
si se activan en Ajustes.

## Cómo se ejecuta en modo desarrollo

```bash
# Crea el .venv con PySide6 la primera vez y arranca la app
./run.sh

# Equivalente a mano
.venv/bin/python src/main.py

# Si la app revienta (un fallo nativo de Qt), deja la pila de Python en el log
.venv/bin/python src/main.py --debug

# Sin ventana: lista las compilaciones disponibles por consola
.venv/bin/python src/main.py --smoke

# Arranca, guarda una captura y sale
.venv/bin/python src/main.py --screenshot /tmp/x.png
```

> Para ver un cambio visual basta con cerrar y volver a arrancar: la app tarda
> menos de un segundo en abrir. El aspecto vive en `src/ui/qss.py`, así que los
> retoques de color o de borde no tocan código de comportamiento.

## ¿Cuál es el archivo principal?

**`src/main.py`**. Es el punto de entrada. Sus tareas, en orden, son:

1. Poner `src/` en el `sys.path` para poder importar `services`, `ui`, `model`.
2. Leer los argumentos (`--smoke`, `--debug`, `--screenshot` y los de uso
   interno `--apply-update` y `--grant-access`). Los modos que no abren ventana
   terminan aquí, **sin** importar Qt.
3. Cargar los ajustes y el idioma.
4. Crear `QApplication` y comprobar que no haya otra instancia abierta (si la
   hay, se le pide que salga al frente y esta se cierra).
5. Cargar la fuente de iconos y aplicar el QSS.
6. Crear `MainWindow`, dimensionarla y mostrarla (o dejarla en la bandeja si se
   pidió arrancar minimizado).

## Mapa rápido del proyecto

```
src/
  main.py            # punto de entrada (empieza a leer por aquí)
  version.py         # número de versión (el CI lo reescribe en los releases)
  paths.py           # rutas: en código fuente vs. empaquetado
  i18n.py            # el mecanismo de traducción (los textos, en locale/)
  locale/*.json      # es, zh, ru, ja, pt_BR (la clave es el texto en inglés)

  model/
    build.py         # los "objetos" de datos: Build y InstalledBuild

  services/          # la lógica de verdad (no saben nada de Qt)
    api.py           # consulta el listado de Blender, lo filtra y lo cachea
    forks.py         # los listados de Bforartists y UPBGE
    detector.py      # detecta el sistema operativo, la arquitectura y Wayland
    settings.py      # ajustes, biblioteca de carpetas y modo portable
    channels.py      # la única definición de "de qué tipo es esta versión"
    downloader.py    # descarga en segundo plano con progreso y SHA-256
    extractor.py     # extrae .tar.xz / .zip de forma segura
    macos_dmg.py     # monta un .dmg y copia el .app (macOS)
    installed.py     # escanea las versiones ya descargadas
    organizer.py     # mueve instalaciones entre carpetas sin poder perderlas
    launcher.py      # lanza Blender como proceso aparte
    recent.py        # los .blend que Blender abrió últimamente
    addons.py        # gestor de addons (listar, activar, instalar, borrar)
    blender_config.py    # dónde guarda Blender la config de cada versión
    blender_addons.py    # migrar addons y ficheros de preferencias
    blender_snapshots.py # los guardados de "valores de fábrica"
    blender_prefs.py     # preferencias por clave (preguntándoselo a Blender)
    blender_runner.py    # arranca Blender en --background para leer/escribir
    blender_style.py     # tema y mapa de teclas como presets
    opener.py        # abre URLs y carpetas con un entorno limpio (AppImage)
    sources.py       # elige la fuente de descarga más rápida
    net.py           # el User-Agent con el que la app se presenta (uno solo)
    tls.py           # contexto TLS con un almacén de CAs que exista de verdad
    elevate.py       # permiso de escritura en Windows (UAC)
    autostart.py     # arrancar con la sesión, en cada plataforma
    updater.py       # comprueba e instala actualizaciones

  ui/                # la interfaz (lo único que conoce Qt)
    qss.py           # el aspecto de TODA la app (una hoja de estilos)
    single_instance.py # abrir la app dos veces trae al frente la que ya corre
    theme.py         # los colores, en constantes
    icons.py         # los glifos de la fuente de iconos
    fonts.py         # carga de la fuente de iconos
    widgets/
      main_window.py   # la cáscara de la ventana, navegación y ciclo de vida
      build_lists.py   # las listas de Nube y Local, filtros y zoom
      settings_view.py # la pantalla de Ajustes
      folder_library.py# la biblioteca de carpetas
      downloads.py     # descargar, instalar, lanzar y borrar versiones
      updates.py       # actualizar BlendTrack
      shell.py         # lo que comparten la ventana y sus partes
      migrate/         # la pantalla de migración, un módulo por pestaña
      addons.py        # el gestor de addons
      recent.py        # los ficheros recientes
      buttons.py       # botones, pastillas, interruptores e iconos
      labels.py        # etiquetas que recortan el texto
      cards.py         # tarjetas de versiones (tienda e instaladas)
      dialogs.py       # diálogos con el aspecto de la app
      layouts.py       # vaciar layouts, listas con scroll, notas apagadas
      menus.py         # menús contextuales con el aspecto de la app
      folders.py       # la fila de una carpeta de la biblioteca
      busy.py          # barra de progreso indeterminada (cargando el listado)
      tray.py          # el icono de la bandeja

  assets/            # logo de Blender, icono de la app y fuente de iconos

tests/               # pruebas automáticas (unittest)
packaging/           # cómo se empaquetan los binarios (PyInstaller, AppImage)
doc/                 # esto que estás leyendo
```

## Cómo se prueban los cambios

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -t . -s tests
```

Las de la interfaz corren con el plugin **offscreen** de Qt, así que no hace
falta pantalla ni servidor gráfico. Cubren el modelo, los servicios (API,
extracción, ajustes, actualizaciones) y la lógica de la ventana que se puede
comprobar sin verla: filtros, columnas de la rejilla, zoom y diálogos.

## Vocabulario que se repite

- **Build**: una compilación concreta publicada por Blender (por ejemplo
  `5.2.1` para Linux x86_64).
- **InstalledBuild**: una build que ya has descargado y extraído en tu carpeta.
- **Vista / View**: cada pantalla (Tienda, Instaladas, Ajustes).
- **Widget**: cualquier "cacharro" de la interfaz (un botón, una etiqueta...).
- **Layout**: la caja que coloca los widgets (fila, columna, rejilla).
- **QSS**: la hoja de estilos de Qt; aquí vive todo el aspecto.
- **`objectName`**: el nombre que le das a un widget para que el QSS lo pinte.
- **Señal**: el evento que emite un widget; se conecta a un método con
  `.connect()`.
