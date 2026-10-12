# 04 - Cómo se añadió el canal "Experimental" (ejemplo completo)

Este capítulo cuenta, paso a paso, cómo se añadió una función real: poder ver y
descargar las **ramas experimentales** de Blender. Sirve de plantilla para
cuando quieras añadir algo tú.

> Regla general: primero el **modelo** si hace falta, luego el **servicio** (la
> lógica), después la **ventana** y por último lo **visual** y los **textos**. Y
> al final, una **prueba**.
>
> Este capítulo cuenta la historia de esa función y enseña el código tal y como
> está hoy, que no es exactamente el de entonces: los canales pasaron de
> pastillas a pestañas, llegaron los forks y los favoritos. Cuando algo cambió
> por el camino, se dice.

## Contexto: dónde están las ramas experimentales

Blender publica compilaciones en dos listados separados:

- Diarias y estables: `https://builder.blender.org/download/daily/?format=json&v=2`
  (el que ya usaba la app).
- Ramas experimentales: `https://builder.blender.org/download/experimental/?format=json&v=2`
  (solo tiene contenido cuando el equipo abre ramas de funciones nuevas).

Los dos devuelven la misma estructura JSON, así que se pueden tratar igual.

## Paso 1 — El modelo (`model/build.py`)

Añadimos un campo para saber si una build es experimental. Con un valor por
defecto, el resto del código no se entera del cambio:

```python
@dataclass
class Build:
    ...
    experimental: bool = False
```

## Paso 2 — El servicio (`services/api.py`)

1. Un segundo endpoint:

```python
EXPERIMENTAL_URL = "https://builder.blender.org/download/experimental/?format=json&v=2"
```

2. `_to_build` acepta si es experimental y lo marca.
3. `fetch_builds` descarga **los dos** listados. El experimental va en su propio
   `try`: muchas veces está vacío o falla, y eso no debe romper el listado
   normal.
4. Se extrae la lógica de filtrado a una función pura y fácil de probar,
   `filter_builds(builds, channel, search="", favorites=())`. Resumida:

```python
if channel == "all":
    selected = list(builds)                 # el catálogo entero
elif channel == "experimental":
    selected = [b for b in builds if b.experimental and not b.fork]
elif channel == "favorites":
    selected = [b for b in builds if b.favorite_key in set(favorites)]
...                                         # lts, stable, daily y cada fork
```

**Decisión de diseño**: las experimentales tienen su pestaña y **no se mezclan
con los canales de tipo** (LTS, Estable, Diarias), para no confundir a quien
solo quiere una versión normal de Blender. La excepción es **Todas**, que
enseña el catálogo entero (experimentales y forks incluidos), porque es lo que
promete su nombre. La primera versión las excluía también de Todas; se cambió
después, y por eso hay que tocar a la vez `api.filter_builds` y
`installed.filter_installed`, o la tienda y las instaladas dirían cosas
distintas del mismo canal.

## Paso 3 — La ventana (`ui/widgets/build_lists.py`)

Las listas de Nube y Local viven en `build_lists.py`, un *mixin* de
`MainWindow`. Como el filtrado ya está en `api.py`, aquí solo hay que llamarlo
(`_filtered`, simplificado):

```python
def _filtered(self):
    builds = api.available_for(self.builds, self.platform, self.arch)
    builds = self.settings.limit_forks(builds)
    return api.filter_builds(builds, self.channel, self.search,
                             self.settings.favorites)
```

Y en la tarjeta (`ui/widgets/cards.py`), si es experimental mostramos el
**nombre de la rama** (o el del programa, si es un fork) como etiqueta en vez
de "Alfa"/"Diaria":

```python
if build.experimental:
    self.channel_text = self.program if self.fork else build.branch
```

> **Cuidado aquí**: no reimplementes el filtro dentro de la ventana. La primera
> versión del port lo repetía a mano y los canales dejaban de filtrar (pasó dos
> veces: en la tienda y en Instaladas). Si `services/` ya tiene una función para
> eso, se usa.

## Paso 4 — Lo visual (`ui/widgets/build_lists.py` + `ui/qss.py`)

En Qt no hay `.kv`. Los canales son excluyentes, así que van en una barra de
pestañas (`QTabBar#ChannelTabs`) que se monta en `_build_filters` a partir de
la constante `CHANNELS` de `ui/widgets/shell.py`. La barra no tiene páginas: al
cambiar de pestaña se llama a `set_channel` y se refiltra la lista de debajo. Añadir un canal es
añadir su fila a `CHANNELS`, con su tooltip en `CHANNEL_TOOLTIPS` (y su clave
en `services.channels.CHANNELS`, que un test mantiene a la par):

```python
CHANNELS = (("all", "All"), ("lts", "LTS"), ("stable", "Stable"),
            ("daily", "Daily"), ("experimental", "Experimental"),
            ...)   # y los forks y Favoritos detrás
```

El aspecto de las pestañas (y de la activa) ya está en `ui/qss.py`, así que un
canal nuevo no necesita nada más. (Al principio los canales eran pastillas,
`Pill`; se pasaron a pestañas porque solo puede haber uno activo.)

Además, cuando el canal está vacío, mostramos un mensaje propio en vez del
genérico "No se encontró ninguna versión" (`_rebuild_store`, en el mismo
`build_lists.py`).

## Paso 5 — Los textos (`src/locale/*.json`)

Toda cadena visible se escribe en inglés como clave dentro de `tr("...")` y se
traduce aparte, en un JSON por idioma (`es`, `zh`, `ru`, `ja`, `pt_BR`). En
`src/locale/es.json`:

```json
"Experimental": "Experimentales",
"No experimental builds right now": "Ahora mismo no hay ramas experimentales disponibles",
```

La misma clave tiene que estar en **todos** los idiomas, y toda clave tiene que
usarse en algún sitio: lo comprueba `tests/test_i18n.py`.

## Paso 6 — La prueba (`tests/test_services.py`)

Al haber sacado el filtrado a una función pura, se puede probar sin abrir la
ventana:

```python
def test_experimental_only_in_its_own_channel(self):
    builds = self._builds()   # incluye una experimental "geometry-nodes"
    experimental = api.filter_builds(builds, "experimental")
    self.assertEqual([build.branch for build in experimental], ["geometry-nodes"])
    # "Todas" las incluye (es literalmente todas); los canales concretos no.
    self.assertIn("geometry-nodes",
                  [b.branch for b in api.filter_builds(builds, "all")])
    for channel in ("lts", "stable", "daily"):
        selected = api.filter_builds(builds, channel)
        self.assertNotIn("geometry-nodes", [build.branch for build in selected])
```

Ejecuta las pruebas:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -t . -s tests
```

## Lo que aprendimos de este ejemplo

- **Añadir un campo con valor por defecto** no rompe lo que ya existe.
- **Separar la lógica de la vista** (la función `filter_builds`) hace que se
  pueda probar. Esa es la razón de que los servicios no sepan de Qt.
- **Un canal nuevo** toca 6 sitios: modelo, servicio, ventana, lo visual,
  textos y pruebas. Teniéndolos localizados, es siempre igual.
- **Fíjate en las decisiones de diseño** (las experimentales no se mezclan con
  los canales de tipo, pero sí salen en Todas). El código no basta; importa
  *cómo se comporta*.

## Un aviso sobre las ramas experimentales

Blender publica builds de ramas de funciones nuevas en el listado
`/download/experimental/`. Antes eran habituales ("Sculpt Dev", "Geometry
Nodes", "Eevee Dof Refactor"...), pero desde ~2021 casi no se publican, así que
ese canal suele estar vacío. Aun así se deja implementado: el día que Blender
abra una rama, aparecerá sola. Es un buen ejemplo de por qué conviene separar
el filtrado en una función pura y probarlo: aunque hoy no haya datos, el código
está listo y verificado.

---

# Anexo - La migración de addons entre versiones

La vista **Migrar** copia los addons de una versión de Blender instalada a
otra, avisando antes de los que no son compatibles. Es una función nueva, no
una variación de las que ya había, así que toca más sitios; se deja aquí como
segundo ejemplo.

## Cómo está repartido

| Capa | Archivo | Qué hace |
| --- | --- | --- |
| Servicio (puro) | `services/blender_config.py` | Localiza la carpeta de configuración de cada versión. **Sin Qt.** |
| Servicio (puro) | `services/blender_addons.py` | Lee `bl_info`/`blender_manifest.toml`, decide compatibilidad y copia (con respaldo y deshacer). **Sin Qt.** |
| Servicio (puro) | `services/blender_snapshots.py` | Los guardados de "valores de fábrica". **Sin Qt.** |
| Servicio (proceso) | `services/blender_runner.py` | Arranca el Blender destino en `--background` para habilitar los addons copiados. **Sin Qt.** |
| Servicio (proceso) | `services/blender_prefs.py` | Le pregunta a Blender sus preferencias clave a clave y se las escribe. **Sin Qt.** |
| Vista | `ui/widgets/migrate/` | Un módulo por pestaña (`addons_tab`, `prefs_tab`, `factory_tab`) sobre una cáscara común (`view.py`). |
| Ventana | `ui/widgets/main_window.py` | Botón de la barra lateral, `set_view`, y refresco con las instaladas. |
| Aspecto | `ui/qss.py` | `#AddonRow`, `#SettingsCard`, `#MigrateTabs` y `#Danger` (la casilla la pinta `CheckPill`, ver abajo). |
| Textos | `src/locale/*.json` | Claves en inglés y su traducción en cada idioma. |
| Pruebas | `tests/test_blender_config.py`, `tests/test_blender_runner.py`, `tests/test_ui.py` | Núcleo, proceso (mockeado) y vista. |

## Las tres decisiones que hay que entender

1. **No se interpreta `userpref.blend`.** Es un `.blend` binario y su estructura
   no es API estable entre versiones. Lo que sea "estado habilitado" o
   "preferencias" se le pregunta al propio Blender, no se escribe a mano.
   Por eso la migración de **preferencias** no está en esta función:
   la parte de addons se puede hacer sin arrancar Blender (leyendo ficheros) y
   solo el *activar* necesita ejecutarlo.

2. **La compatibilidad es conservadora.** Un addon se bloquea si pide una
   versión más nueva (`blender_version_min`), si su `blender_version_max` ya se
   pasó, o si no está publicado para este sistema. Se marca para revisar si sus
   wheels son de otro Python o si no declara mínimo (no se puede saber). Los
   bloqueados **no se pueden marcar** en la interfaz: nunca se copian sin
   querer. La lógica vive en `compat_report`, una función pura con tests.

3. **Se imita el estado activado del origen.** No se activa todo por migrar: se
   pregunta al Blender origen qué addons tenía **habilitados** (una sola
   arrancada, `blender_runner.enabled_addons`) y solo esos se activan en
   destino. Los que allí estaban apagados se copian apagados, que es lo que el
   usuario espera de una copia. Por eso no hay interruptor de "activar tras
   copiar".

4. **Copiar nunca borra.** Si el addon ya existe en destino, se aparta con el
   sufijo `.blendtrack-bak` y se anota en `.blendtrack-migration.json`,
   que es lo que permite **deshacer** la última migración. Es el mismo espíritu
   que `write_problem` (`ui/widgets/shell.py`) o los guardados atómicos de
   `settings.py`.

   Ojo con el detalle de los respaldos: **hay uno solo por destino, y se
   reutiliza**, porque los usuarios migran más de una vez (a veces en la
   dirección contraria) y con un sufijo por fecha se acumulaba un `.bak` por
   intento sin que nadie los viera. Lo que interesa conservar es el estado
   *inmediatamente anterior* al último cambio, no un histórico. El marcador se
   borra al deshacer, así que un segundo "deshacer" (doble clic típico) no
   restaura nada de más.

## Cómo está maquetada la pantalla

`MigrateView._build_ui` (`ui/widgets/migrate/view.py`) calca la estructura de
Ajustes, para que las dos pantallas se lean igual:

1. **Pestañas arriba del todo** (`#MigrateTabs`), alineadas por arriba con la
   barra lateral y con las pestañas de canal (`theme.TABS_TOP`). Van
   estilizadas en `ui/qss.py`, porque si no salen con el estilo claro del
   escritorio. Antes había una cabecera propia con título; se quitó al calcar
   Ajustes.
2. **Tarjeta de versiones** (origen → destino, con el aviso de "Blender está
   abierto"). Es **un solo widget** común a Add-ons y Preferencias que se
   cambia de padre a la pestaña visible (`_show_header`): con una copia por
   pestaña habría varios pares de desplegables que mantener sincronizados. La
   pestaña de fábrica lleva su propia tarjeta con **un solo** desplegable,
   porque no migra nada: un "Desde" y un "Hacia" harían creer que usa los dos.
3. **El contenido de cada pestaña**:
   - **Add-ons**: el tablero y, **debajo**, el resumen de compatibilidad y la
     explicación. Van dentro de la pestaña: cuando compartían tarjeta con las
     versiones, ocultarlos al cambiar de pestaña movía toda la interfaz.
   - **User prefs**: primero el detalle fino (ajustes uno a uno, cada uno con
     el nombre y la descripción que les da Blender), luego el tema y el mapa
     de teclas como presets, y al final los ficheros completos (el atajo que
     lo pisa todo).
   - **Factory settings**: pestaña propia porque no migra nada, hace otra cosa
     (deja una versión como recién instalada, con tus ajustes guardados aparte).

Se probó a anidar pestañas dentro de la de preferencias (detalle/ficheros vs
fábrica) y se descartó: pestañas dentro de pestañas se leen mal. Tres pestañas
planas dicen lo mismo sin confundir.

### El efecto de capas (fondo oscuro + tarjetas elevadas)

Migración sigue el mismo esquema que Nube, Local y Ajustes: el fondo de cada
pestaña es oscuro (`BG`, el de la ventana) y encima van las **tarjetas** un
escalón por encima (`SURFACE`, #303030) con la misma sombra que las de la tienda
(`cards.card_shadow`, que ya pone `settings_card`). De la vista en sí
(`QWidget#MigrateView`, en `SURFACE`) solo se ve la fila de detrás de las
solapas. Las filas de addons van *dentro* de una tarjeta, así que bajan un
escalón (`CARD_DIM`/`CARD_DIM_ALT`) para no fundirse con ella.

Al principio toda la vista iba en gris de arriba abajo y era la única pantalla
con el fondo claro; se cambió para que todas siguieran el mismo esquema. Las
trampas de QSS que salieron por el camino siguen valiendo:

- **El *pane* del `QTabWidget` es la única parte que pinta fondo.** Las páginas
  son `QWidget` pelados y `background: transparent` en un `QWidget` pelado no
  siempre deja pasar el fondo de debajo; por eso cada página lleva su color
  **directo** (`QWidget#MigratePage { background-color: BG }`).
- **Un `QWidget` pelado no hereda el fondo de su padre**: la regla global
  `QWidget { background-color: BG }` le pinta el suyo. Si tiene que llevar
  otro, necesita su propia regla (como `QWidget#MigrateHeader`).
- **Ojo con la especificidad**: `QWidget#MigrateView QFrame#AddonRow` (dos
  ids) gana a `QFrame#AddonRow[zebra="true"]` (un id y un atributo). Por eso la
  variante de cebra también se declara con el prefijo de la vista, o todas las
  filas saldrían del mismo color. Es el tipo de trampa que avisa `AGENTS.md`
  sobre el orden y la especificidad en QSS.
- **Una subclase de `QWidget` no pinta el fondo del QSS** salvo que active
  `setAttribute(Qt.WA_StyledBackground, True)`. `MigrateView` es subclase, así
  que sin eso su fondo no se dibujaba: la zona de la fila de pestañas a la
  derecha de las solapas (que no pinta nadie) dejaba ver el oscuro de detrás.
  Se detectó pintando la regla de magenta como prueba: el QTabWidget tampoco la
  cogía, señal de que el hueco era del padre transparente.
- **La sombra se recorta si el padre mide exactamente lo mismo que la tarjeta.**
  `QGraphicsDropShadowEffect` no puede pintar fuera del padre, así que el
  tablero NO va dentro de un `QWidget` ajustado a su tamaño: su layout cuelga
  directamente de la página (que es grande) y así la sombra cae en el hueco.
  De paso, las tarjetas ya no se estiran a lo alto: miden lo que miden sus
  filas.

### El tablero (por qué dos columnas)

El primer boceto era una lista con casillas. Se cambió a **origen a la
izquierda / destino a la derecha con una flecha de un solo sentido** porque
enseña de un vistazo *qué* se copia y *dónde* cae cada cosa. Detalle importante:
las dos columnas **comparten una única área de scroll**; así las filas quedan
alineadas sin sincronizar dos barras. Las filas miden `ROW_HEIGHT` (48 px) fijo
para que las dos mitades cuadren.

## El proceso: lo que costó

- `addon_utils.enable()` con `default_set=True` es lo que persiste el estado.
  Se probó de verdad contra Blender 5.2: el addon queda habilitado al reiniciar.
- **Los glifos de la fuente de iconos no valen como texto de botón** (salen como
  un recuadro): van dentro de un `QIcon` con `ui.fonts.glyph_icon`.
- **`deleteLater()` no es inmediato**: al recargar el tablero, las filas viejas
  seguían en el layout y salían duplicadas. Hay que hacer `setParent(None)` +
  `hide()` además de `deleteLater()`.
- El `BLENDER_USER_CONFIG` del entorno **no** reubica `scripts/`: los addons
  legacy cuelgan de `BLENDER_USER_SCRIPTS` o de la carpeta de la versión. Por
  eso `config_for` resuelve las tres rutas por separado.
- **La palomita del checkbox no se puede hacer por QSS.** Se intentó con un SVG
  en ``::indicator:checked { image: ... }`` (que aislado pinta perfecto) y en la
  app salía sin palomita: la regla global `QWidget { background-color }`
  **hereda** sobre el `::indicator` y Qt descarta el `image`. Da igual usar
  `background` o `background-color`, y tampoco ayuda un `border-radius` en la
  base. La salida es `CheckPill` (`ui/widgets/buttons.py`), que pinta el cuadro
  y el trazo con `QPainter`, igual que `SwitchPill`.

## Lo que aprendimos

- Una función nueva "grande" se apoya en lo que ya hay: `clean_env()` para
  lanzar Blender, `ElidedLabel` para los nombres, `SettingsCard`/`CardButton`
  para el aspecto, el patrón `threading.Thread` + señal de `Downloader` para el
  trabajo en segundo plano.
- **Probar el runner con Blender de verdad** merece la pena una vez: confirmó
  que 5.1 usa Python 3.13. La versión de Python de cada serie sale de una
  tabla (`_PYTHON_BY_SERIES`, consultada con
  `blender_config.python_for_version`) y no de arrancar el Blender destino, a
  propósito: el plan de migración se recalcula en cada cambio de desplegable y
  arrancar Blender cada vez son segundos de espera para un dato que cambia una
  vez cada pocos años.
- Lo que no se puede hacer sin riesgo, no se hace: el `startup.blend` y los
  keymaps no se tocan a mano; cuando se migran, delegan en Blender.

## Preferencias selectivas (comparar con los valores de fábrica)

`services/blender_prefs.py` resuelve la parte fina: en vez de copiar el
`userpref.blend` entero, le pregunta al Blender **origen** por sus preferencias
y por las de fábrica:

- `read_preferences(exe)` vuelca el árbol de preferencias recorriendo las
  propiedades RNA (`view.ui_scale`, `inputs.use_zoom_to_mouse`...). Se salta
  colecciones y lo de solo lectura; los punteros (`view`, `edit`) se recorren
  para bajar, no se copian.
- `read_preferences(exe, factory=True)` hace lo mismo con `--factory-startup`.
- `diff(user, factory)` deja **solo lo que el usuario cambió** (medido: 11 de
  280 claves en esta máquina) y `environment_preferences` aparta lo que depende
  del equipo (`system.gpu_backend`, rutas de temp...), que se ofrece sin marcar.
- `write_preferences(exe, claves)` escribe **clave a clave** en la versión
  destino y devuelve `{"applied", "errors", ...}`. Ese informe es el "feedback
  de incompatibles": una preferencia que Blender renombró o hizo de solo
  lectura sale listada en vez de romper la migración entera.

Detalles que costó aprender:

- La ruta RNA **no es API estable**: por eso se aplica clave a clave y no se
  copia el fichero con las claves interpoladas.
- Los enums de varios valores (p. ej. `edit.key_insert_channels`) llegan como
  `set`, y `str()` sobre un `set` no tiene orden estable entre procesos: se
  vuelcan como lista **ordenada** (si no, el diff los daría por cambiados sin
  haberlos tocado) y al escribir se convierten de nuevo en `set`.
- Un test de verdad (Blender 5.2) enseñó que 10 de 11 claves aplican y una
  (`view.show_statusbar_vram`) es de solo lectura en esa versión: el informe la
  enseña en vez de fallar.
- **La lectura es automática** al elegir la versión de origen:
  `_read_source_blender` (`ui/widgets/migrate/prefs_tab.py`) guarda para qué
  versión se hizo (`_source_read_for`) y no repite la lectura mientras no
  cambie el origen. El botón "Read again" solo fuerza un reintento.
- El **estado activado de los addons** y las **preferencias** (las del usuario
  y las de fábrica) se piden juntos, en el mismo hilo y una sola vez por origen
  (`enabled_addons` + `read_preferences`), en vez de en dos momentos
  distintos.

## Reset a valores de fábrica (recuperable)

`set_config_aside` / `restore_snapshot` / `all_snapshots` / `delete_snapshot`
(en `services/blender_snapshots.py`) hacen lo que mucha gente hace a mano: **mover la carpeta `config`** para probar
una versión "de fábrica". La diferencia es que la apartan a una instantánea con
fecha (`.blendtrack-snapshots/config-<fecha>-<etiqueta>`) en vez de
renombrarla, así que:

- se puede **recuperar** los ajustes anteriores (y al recuperar, la config
  limpia se aparta también: el propio "restaurar" es reversible),
- se pueden tener **varias instantáneas** (resetear y reintentar es normal),
- y borrarlas para dejarlo limpio **para siempre**, que es la otra opción.

**Hallazgo importante:** `bpy.ops.wm.save_userpref()` **no crea** la carpeta
`config` si no existe (comprobado en Blender 5.2 en `--background`: falla en
silencio). Tras un reset esa carpeta puede faltar, así que `blender_runner`
la asegura antes de lanzar Blender; sin eso, activar addons después de un reset
no se guardaba.
