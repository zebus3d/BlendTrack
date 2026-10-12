# 01 - Arquitectura

El proyecto está dividido en **capas**. Cada capa solo habla con la de al lado.
Esto es lo que hace que puedas cambiar una parte sin romper las demás.

```
   ┌───────────────────────────────────────────────────────────┐
   │  interfaz (ui/)                                            │
   │  - qss.py      -> aspecto (hoja de estilos)                │
   │  - widgets/    -> la ventana (main_window.py y sus mixins) │
   │                   y los widgets reutilizables              │
   └───────────────────────────┬───────────────────────────────┘
                               │  llama a funciones
   ┌───────────────────────────▼───────────────────────────────┐
   │  servicios (services/)                                     │
   │  api, forks, settings, channels, downloader, extractor,    │
   │  installed, launcher, updater, blender_*... (unos 25)      │
   └───────────────────────────┬───────────────────────────────┘
                               │  crea y usa
   ┌───────────────────────────▼───────────────────────────────┐
   │  modelo (model/build.py)                                   │
   │  Build, InstalledBuild                                     │
   └───────────────────────────────────────────────────────────┘
```

- El **modelo** son solo datos. No sabe nada de Qt ni de red.
- Los **servicios** hacen el trabajo (descargar, extraer, consultar la API,
  guardar ajustes...). Tampoco saben nada de Qt: se pueden probar sin abrir una
  ventana. Por eso los `tests/` los prueban directamente.
- La **interfaz** usa esos servicios y los pinta. Es la única capa que conoce
  Qt.

> Esa frontera no es decorativa: es lo que permitió portar la interfaz entera
> (de Kivy a Qt Widgets) sin tocar ni una línea de `services/`. Si te ves
> importando `PySide6` dentro de `services/`, algo se está torciendo.

## El modelo: `model/build.py`

Dos dataclasses sencillas:

- **`Build`**: una compilación publicada por Blender (o por un fork). Campos:
  `version`, `branch`, `risk`, `platform`, `arch`, `url`, `filename`,
  `checksum`, `build_hash`, `experimental`, `fork`... y un par de propiedades
  útiles como `is_lts` o `human_size` (`"359.8 MB"`).
- **`InstalledBuild`**: una versión ya descargada. Guarda `name`, `path`,
  `executable`, `version`, `build_hash`, `branch`, `risk`, `root` (la carpeta
  de la biblioteca donde está) y `fork`.

Al ser dataclasses, crear una es directo: `Build(version="5.2.1", ...)`.

## Los servicios

`services/` tiene unos 25 módulos, todos sin Qt. Agrupados por tema:

| Tema | Archivos | Qué hacen |
|---|---|---|
| Listados | `api.py`, `forks.py`, `sources.py` | Descargan los listados de Blender (diarias y ramas experimentales) y los de Bforartists y UPBGE, los cachean y los filtran; `sources` elige de dónde bajar cada archivo. `api.filter_builds` es una **función pura**: la UI la usa, no la reimplementa. |
| Red | `net.py`, `tls.py`, `downloader.py` | `net` guarda el único `User-Agent` de la app; `tls` da un contexto TLS con un almacén de CAs que existe de verdad; `downloader` baja en un hilo, a un `.part`, con progreso y SHA-256, y renombra solo al terminar. |
| Disco | `extractor.py`, `macos_dmg.py`, `installed.py`, `organizer.py`, `channels.py` | Extraer (comprobando rutas maliciosas, el "zip slip"), instalar el `.app` de un `.dmg`, escanear lo instalado (con el marcador `.blendtrack.json`), mover entre carpetas sin perder nada y decidir de qué tipo es cada versión. |
| Sistema | `detector.py`, `launcher.py`, `opener.py`, `elevate.py`, `autostart.py` | SO, arquitectura y sesión gráfica; lanzar Blender como proceso independiente; abrir URLs y carpetas con un entorno limpio; pedir permiso de escritura en Windows (UAC); arrancar con la sesión. |
| Ajustes | `settings.py`, `recent.py` | Ajustes en JSON guardados de forma atómica (con **modo portable**: un archivo `portable` junto al binario) y los `.blend` recientes de cada versión. |
| Config de Blender | `blender_config.py`, `blender_addons.py`, `blender_prefs.py`, `blender_snapshots.py`, `blender_style.py`, `blender_runner.py`, `addons.py` | Dónde vive la config de cada versión, migrar addons y preferencias, los guardados de "valores de fábrica", tema y keymap como presets, arrancar Blender en `--background` y el gestor de addons. |
| La propia app | `updater.py` | Comprueba e instala actualizaciones desde GitHub Releases. En modo fuente no descarga nada: actualiza el checkout con `pull --ff-only` y reinicia. |

## La interfaz

- **`ui/qss.py`**: el aspecto de toda la aplicación (una hoja de estilos Qt
  construida con los colores del tema). Sustituye a los antiguos `views/*.kv`.
- **`ui/theme.py`**: los colores (paleta oscura tipo Blender) como constantes.
- **`ui/icons.py`**: los glifos de la fuente Font Awesome como constantes.
- **`ui/fonts.py`**: carga la fuente de iconos y devuelve `QFont`/`QIcon`.
- **`ui/single_instance.py`**: que abrir la app dos veces traiga al frente la
  que ya corre en vez de crear otra ventana.
- **`ui/widgets/`**:
  - `main_window.py` — `MainWindow`: la cáscara (cabecera, lateral, pie,
    navegación, bandeja y ciclo de vida). No lo hace todo ella: hereda de
    varios **mixins**, uno por responsabilidad, que trabajan sobre el estado
    de la misma ventana:
    - `build_lists.py` (`BuildListsMixin`) — Nube y Local: pestañas de canal,
      filtros, rejilla, zoom y refiltrado.
    - `downloads.py` (`DownloadFlowMixin`) — descargar, instalar, lanzar y
      borrar versiones.
    - `updates.py` (`UpdateFlowMixin`) — actualizar la propia aplicación.
    - `settings_view.py` (`SettingsViewMixin`) — la pantalla de Ajustes.
    - `folder_library.py` (`FolderLibraryMixin`) — la biblioteca de carpetas.
  - `shell.py` — lo que comparten la ventana y sus mixins (constantes,
    `WorkerBridge`, `write_problem`...); existe para romper la importación
    circular entre la ventana y sus partes.
  - `migrate/` (la pantalla de migración: `view.py` y un módulo por pestaña),
    `addons.py` y `recent.py` — otras pantallas.
  - `buttons.py`, `cards.py`, `dialogs.py`, `labels.py`, `layouts.py`,
    `menus.py`, `folders.py`, `busy.py`, `tray.py` — widgets reutilizables
    (botones, tarjetas, `AppDialog` y `confirm()`/`show_error()`, etiquetas
    que recortan, layouts, menús, la fila de una carpeta, la barra
    indeterminada y el icono de la bandeja).
  - `__init__.py` — vuelve a exportar todo, así `from ui.widgets import X`
    funciona sin saber en qué archivo vive cada cosa.

¿Por qué mixins y no objetos separados? Porque todas esas partes leen y
escriben el mismo estado (la lista de builds, los ajustes, la barra de
progreso). Meterlas en objetos independientes solo cambiaría el acoplamiento de
sitio; lo que de verdad no depende de la interfaz ya está en `services/`. Y así
ningún módulo pasa de 1000 líneas (lo vigila `tests/test_layout.py`).

## Cómo viaja la información (flujo de datos)

**Del servidor a la pantalla (la tienda):**

```
api.get_builds()             descarga el JSON (y el de los forks activos) y
        │                     lo cachea; corre en un hilo (build_lists.refresh)
MainWindow.builds = [...]     lo guarda la ventana, si es la respuesta a la
        │                     última petición (_on_refresh_result)
        │
api.available_for(...)        filtra por plataforma y arquitectura
        │
api.filter_builds(...)        filtra por canal y búsqueda
        │
_rebuild_store()              crea una tarjeta por compilación
```

**De la descarga al disco:**

```
install_build(build)          downloads.py
        │
downloader.start(...)         descarga en un hilo (no bloquea la ventana)
        │  (al terminar avisa emitiendo la señal WorkerBridge.done)
_on_download_done(...)        downloads.py: decide cómo instalar
        │
extractor.extract(...)        extrae el archivo (o macos_dmg, si es un .dmg)
        │
installed.write_marker(...)   anota el hash de la build
        │  (señal extract_done)
refresh_installed()           vuelve a escanear y repinta
```

## Hilos: la regla que no se puede romper

La red y el disco pueden tardar. Si los ejecutas en el hilo de la interfaz, la
ventana se queda **congelada**. Por eso todo lo lento va en un hilo aparte
(`threading.Thread`) y, al terminar, vuelve al hilo de la interfaz **emitiendo
una señal Qt** (que el receptor encola):

```python
# Simplificado de build_lists.refresh
def worker():
    builds = api.get_builds(force=force, forks=forks)
    self.builds_loaded.emit(request, forks, builds)  # señal -> slot en el hilo de la UI

threading.Thread(target=worker, daemon=True).start()
```

El `request` es un número que crece con cada petición: si el usuario refresca
dos veces seguidas, las respuestas pueden llegar en cualquier orden, y
`_on_refresh_result` descarta la que no sea la última pedida.

Los callbacks de descarga se envuelven en `WorkerBridge` (`shell.py`) por el
mismo motivo. **Nunca** toques un widget desde otro hilo (Qt revienta igual que
reventaba Kivy).

## Código fuente vs. empaquetado

`paths.py` resuelve la diferencia:

- **Modo fuente** (`./run.sh`): las rutas salen de `src/`.
- **Empaquetado** (PyInstaller): los recursos se extraen a una carpeta temporal
  (`sys._MEIPASS`) y el ejecutable está en `APP_DIR`.

No tienes que preocuparte por esto: usa siempre `APP_DIR`, `RESOURCE_DIR` y
`ASSETS_DIR` de `paths.py`.

## El sistema de actualización

`updater.py` consulta `.../releases/latest` de GitHub. Si hay versión nueva,
la descarga **comprobando su SHA-256** contra el `checksums.txt` de la misma
release (si no lo hay, no instala nada y ofrece la página de releases) y la
aplica según la plataforma:

- **AppImage (Linux)**: sustituye el propio archivo `$APPIMAGE` y se relanza.
- **Windows**: extrae la release a un directorio temporal y relanza el binario
  nuevo con `--apply-update`.
- **macOS**: extrae el zip con `ditto` y lanza un pequeño *helper* (al estilo
  de Sparkle) que espera a que la app se cierre, aparta el bundle viejo, copia
  el nuevo, le quita la cuarentena y relanza. Si no puede (sin permiso en la
  carpeta del bundle, por ejemplo), revela la descarga y avisa.
- **Modo fuente**: no hay binario que reemplazar. Al arrancar **no** avisa
  (comparar un checkout con la última release no dice nada: puede ir por
  delante), y el *check* manual hace `git pull --ff-only` y reinicia.

Cada push a `main` actualiza un **único pre-release** (siempre el mismo tag,
reemplazando los binarios). El auto-update mira `releases/latest`, que ignora
los pre-releases, así que mientras se itera nadie recibe nada; cuando el
pre-release se da por bueno se promueve a release final sin reconstruir nada.
