# 03 - Diseño de la interfaz

La app imita el **tema oscuro por defecto de Blender**. Si respetas estas
reglas, cualquier cosa que añadas parecerá parte del programa.

## La idea de profundidad

Cuanto más oscuro, más "hundido"; cuanto más claro, más "elevado". Como en
Blender:

```
BG / FIELD   (más oscuro)  ->  fondo de la ventana y campos de texto
SURFACE/CHROME             ->  barras y tarjetas
BUTTON / SURFACE_ALT (más claro)  ->  botones y resaltados
```

Los colores están en `src/ui/theme.py` y el QSS los usa con f-strings
(`{t.ACCENT}`). **No se ponen colores a mano**: siempre desde `theme`.

## Paleta (lo esencial)

**La fuente de verdad es `src/ui/theme.py`**: cada color lleva al lado su
contraste medido y el porqué (y `ContrastTests` los vigila). Esta tabla es un
resumen para orientarse; si no cuadra con `theme.py`, manda `theme.py`.

| Constante | Color | Para qué |
|---|---|---|
| `BG` | `#1D1D1D` | Fondo de la ventana (lo más oscuro) |
| `FIELD` | `#171717` | Campos de texto (hundidos) |
| `CARD_DIM` / `CARD_DIM_ALT` | `#262626` / `#202020` | Tarjeta de la tienda que no tienes |
| `SURFACE` / `CHROME` | `#303030` | Paneles, tarjetas instaladas, barras |
| `SURFACE_ALT` | `#3D3D3D` | Resaltado al pasar el ratón |
| `BUTTON` | `#585858` | Botones neutros (gris de pestaña de Blender) |
| `TEXT` / `MUTED` / `TEXT_DIM` | `#E6E6E6` / `#A8A8A8` / `#B4B4B4` | Texto principal / secundario / título de lo no instalado |
| `ACCENT` | `#5085B1` | Azul de **bordes e indicadores** (3:1) |
| `ACCENT_SELECTED` / `ACCENT_BTN` | `#487AA3` / `#325B82` | El azul cuando lleva **texto** encima (4,5:1) |
| `WARNING` | `#FFAF23` | Naranja (LTS) |
| `DANGER` / `DANGER_BTN` | `#B84A4A` / `#983A3A` | Rojo de borde / relleno del botón borrar |
| `SUCCESS_TEXT` | `#6FCF7A` | Verde claro para texto "Instalada" |
| `INFO_TEXT` | `#80ACE2` | Azul claro para etiquetas de canal |
| `TESTING` | `#8E6CD8` | Violeta: luz de las versiones en pruebas (solo luz, nunca texto) |

> Importante: los colores de texto llevan el sufijo `_TEXT` porque son más
> claros que los de fondo o de borde del mismo tono (`INFO_DISC`, `ACCENT`):
> un verde o un azul pensados para rellenar se leen mal como texto sobre una
> tarjeta. Por lo mismo, **el azul de un borde no sirve de relleno con texto**:
> blanco sobre `ACCENT` se quedaba en 3,94:1, y por eso existen
> `ACCENT_SELECTED` y `ACCENT_BTN`.

Lecciones que dejó medir en vez de mirar (están explicadas en `theme.py`):

- El texto se mide en su **peor fondo**, no en el habitual: `MUTED` pasaba
  sobre la tarjeta pero bajaba a 3,77:1 con el ratón encima (`SURFACE_ALT`).
- Un botón con degradado se mide en su **punto más claro** (el 9 % de blanco
  de arriba).
- Pulsar **oscurece**: el botón primario antes se aclaraba al pulsar y el texto
  bajaba a 2,72:1.
- Antes de dar color a algo nuevo, mira qué significa ya: el verde es
  "instalada / lanzar", así que ni las experimentales ni los forks llevan verde
  (y los forks no usan el color de su logo porque coincidía con estable y LTS).

## Cómo se comunica el estado

| Estado | Cómo se pinta |
|---|---|
| Normal | Color bajo (FILTER, BUTTON, CARD_DIM) |
| Hover | Un poco más claro (SURFACE_ALT, ACCENT_DARK) |
| Activo/seleccionado | ACCENT a tope + texto claro (`TEXT_SEL`) |
| Deshabilitado | Texto apagado (`rgba(230,230,230,0.35)`) |
| Destructivo | Rojo DANGER |

El resaltado al pasar el ratón sale de la propiedad dinámica `hover`, que pone
`_HoverCard` (`ui/widgets/cards.py`), y del selector `:hover` del QSS. **Los
`:hover` van al final** del bloque en `qss.py`: en QSS
`#Card[installed="true"]` y `#Card:hover` empatan en especificidad, y gana la
última.

## Espaciado y tamaños

- **Márgenes exteriores**: 14 a 16 px.
- **Márgenes interiores de tarjeta**: 12 a 14 px (escalan con el zoom).
- **Separación entre hermanos**: 10 px en rejillas, 6 px en grupos juntos.
- **Alto de la cabecera**: 72 px. **Barra de filtros**: 50 px
  (`theme.FILTERS_HEIGHT`). **Barra lateral**: 74 px.
- Se usan **píxeles enteros**: `setContentsMargins(14, 14, 14, 14)`. No hay
  `dp()` como en Kivy; lo único que escala es el **zoom** de la rejilla, que se
  aplica calculando los valores (el alto de una tarjeta de rejilla sale de
  `_grid_height(zoom)` en `ui/widgets/cards.py`; las de lista miden 68 px
  fijos).

## La pantalla, en tres zonas

```
+--------[ Cabecera: 72 px ]--------+   logo, título, buscador, refrescar
| barra +--[ Filtros:  50 px ]-------+   pestañas de canal, vista, plataforma...
| late- |                             |
| ral   |        contenido            |   Nube / Local / Migración / Ajustes
|       |                             |
+--------[ Pie:    ~40 px ]---------+   estado, progreso, zoom
```

La cabecera, la barra de filtros y el pie tienen alto fijo (`setFixedHeight`). El
cuerpo se queda con el espacio que sobra. La fila de filtros **solo existe en
Nube y Local** (vive con las listas, en `build_lists.py`); Migración y Ajustes
no la llevan.

## Reglas de lectura

- Título de tarjeta: `QLabel#Title`, 16 px y negrita.
- Texto normal: 13 px (el `font-size` base del QSS).
- Texto secundario: `QLabel#Muted`, en gris.
- El **tamaño de letra del QSS pisa** cualquier `setFont` hecho desde Python: si
  algo tiene que verse más grande, se hace con una regla en `qss.py`.
- Para un botón que solo lleva un icono (la papelera), el QSS le quita el
  padding lateral (`[iconOnly="true"]`). Con el padding normal, a zoom bajo el
  glifo no cabía y quedaba un recuadro rojo vacío.

## Accesibilidad (lo que ya está cuidado)

- Zonas pulsables de al menos ~24 px (los botones redondos son 50 px; el círculo
  de información, 24 px, el mínimo de WCAG 2.5.8).
- El color **no** es el único indicador: las instaladas llevan también el rótulo
  "Instalada" y su tarjeta tiene otro fondo; las que no tienes, además del fondo
  apagado, llevan el **logo atenuado**.
- Contraste bueno: texto claro sobre superficies oscuras (los pares medidos
  están anotados en `theme.py`).

## Si vas a cambiar algo visual

1. Toca primero `src/ui/theme.py` si es un color repetido.
2. El aspecto de cada widget está en `src/ui/qss.py`. Busca la regla por su
   selector: `QPushButton#CardButton`, `QFrame#Card`, `QLabel#Title`...
3. Si el widget es nuevo, dale un `objectName` en el código y añade su regla al
   QSS. Para variantes, una **propiedad dinámica** (`[zebra="true"]`,
   `[iconOnly="true"]`, `[variant="danger"]`).
4. Arranca con `./run.sh` y mira el resultado; para probar sin pantalla, el
   plugin *offscreen* de Qt (ver el capítulo 02).

## Decisiones de diseño (y por qué)

Los principios generales (contraste, estados, tooltips, movimiento) están en la
skill `ux-ui-expert`; aquí, cómo se aplicaron en BlendTrack.

- **Tooltips en todo** (2026-09-16): botones, campos, desplegables,
  interruptores y sliders. Un test falla si un filtro, control principal o
  botón de tarjeta se queda sin él. Explican el para qué y el argot: "LTS"
  dice *"LTS = soporte a largo plazo… versiones mantenidas durante años y las
  más estables. Recomendadas para trabajar a diario."*; "Diarias" avisa de
  que pueden fallar. Un `\n` hace salto de línea.
- **Nada de botones "Sí/No"**: los ajustes son interruptores pintados a mano
  (pista + bolita), con hover, pulsado, activo, deshabilitado y aro de foco.
- **Un interruptor por decisión**: "Iniciar automáticamente al iniciar sesión"
  e "Iniciar minimizado en la bandeja" van separados.
- **Lo que la plataforma no puede cumplir no se ofrece roto**: minimizar a la
  bandeja en Wayland/KDE o el autoarranque no soportado se deshabilitan
  diciendo el motivo; si algo necesita reiniciar, se avisa.
- **Color de la luz de la galería** (2026-10-09): naranja = LTS (el mismo
  `WARNING` del rótulo "LTS"), azul = estable, violeta (`TESTING`) = diarias,
  alfas, beta y experimentales; turquesa y magenta para los forks.
- **Galería**: cartas verticales ~1:1,55 cuyo ancho sale del alto disponible
  (con mínimo y máximo) y 22 px de aire al techo; las lejanas desaturan ~45 %
  y oscurecen ~16 % por puesto. La carta inclinada sigue dejando pulsar sus
  botones. El fondo lleva *dithering* contra el banding.
- **Tiempos**: Ajustes baja en 280 ms, la tarjeta "se levanta" en 160 ms, la
  estrella hace pop en 420 ms; siempre `OutCubic`.
- **Efectos desactivables** en Ajustes > Interfaz: inclinación, sonidos (con
  silencio a mano en la propia vista) y láser. Sonido en dos capas: tic al
  pasar, "clac" al seleccionar, volumen bajo.
- **Línea de atajos al pie** de la galería (← → ↑ Intro 1–6), recortada con
  "…" antes que meterse bajo los botones de la esquina.
- **Popups al estilo Blender**: colores reales de sus popovers
  (`wcol_menu_back` en `release/datafiles/userdef/userdef_default_theme.c`,
  comprobado 2026-10-10): fondo `#181818`, borde `#242424`, más
  `rgba(24,24,24,235)` y sombra suave. Dentro, el carril del deslizador va en
  `SURFACE_ALT` (en `FIELD` se perdía).
- "LTS + Stable" se quitó de la barra de filtros: era el más largo y
  redundante, y desbordaba a 880 px en español.
- El código y el rendimiento de los efectos: skill `pyside6-expert` §11.

## Capturas de documentación (`docs/img/`)

- **1280×880**, en inglés (el idioma se fija en los ajustes temporales, no
  depende del locale), con la barra de filtros visible. Rejilla y lista
  (`store_grid`/`store_list`, `installed_grid`/`installed_list`).
- Una vista poblada, al menos una versión de cada tipo, un filtro activo; sin
  tooltips, popups ni descargas en curso. `--screenshot` espera 6 s a los datos.
- Con la **ventana real**: en *offscreen* pueden salir zonas negras si el
  tamaño no cuadra con el framebuffer.
- Los estados que no se dan solos se provocan por código, p. ej.
  `root._show_update_available("v1.2.0", {...})`.

## Fallos visuales ya diagnosticados

| Síntoma | Causa y arreglo |
|---|---|
| El elemento no se resalta al pasar el ratón | (a) `#Card[installed="true"]` y `#Card:hover` empatan y gana la última regla → los `:hover` al final; (b) el padre no recibe hover con el ratón sobre un hijo → propiedad dinámica en `enterEvent`/`leaveEvent` (+ `unpolish`/`polish`) |
| Recuadro de fondo alrededor de un bloque | Un `QWidget` contenedor hereda el fondo de `QWidget { ... }`: `objectName` + `background: transparent` (cabecera, `QSlider`) |
| Sale un cuadro (tofu) en vez de un icono | Qt no mezcla fuentes en el texto de un control: `QIcon` (`glyph_icon()`) o un botón que **es** icono |
| La barra de progreso aparece sin descarga | `QProgressBar`/`QSlider` se ven por defecto: `setVisible(False)` hasta que apliquen |
| La última tarjeta de la rejilla se corta | Más columnas de las que caben al ancho mínimo real (227 px medidos): `max(ancho_objetivo, 230)` |
| El diálogo sale con el estilo claro del escritorio | `QMessageBox`/`QDialog` no siguen el tema: `AppDialog` propio |
| La barra de filtros se sale por la derecha | Controles de ancho fijo que no envuelven: probar a 880 px (mínimo de la ventana) y en español |
| Un canal poco frecuente siempre sale vacío | Estado vacío propio ("no hay ramas experimentales ahora mismo") |
| Las tarjetas no miden igual | Un `QLabel` pide como mínimo su texto completo (483 px → tarjeta de 507 frente a 249): `ElidedLabel` |
| El diálogo corta el texto | Qt mide el `QLabel` con `wordWrap` antes de aplicar la fuente del QSS (90 px cuando necesitaba 108): recalcular el alto en `resizeEvent` + ancho máximo |
| Los logos de lo no instalado no se atenúan | `opacity` en QSS no afecta a un `QLabel` con pixmap: pintar la opacidad en el pixmap |
| Mucho hueco entre refrescar y el buscador | El bloque se estiraba (97 px a 1500 px de ancho): `QSizePolicy.Fixed` + `Qt.AlignRight` |
| Los botones se ven planos | El degradado se mezcla en el color (QSS no apila fondos) |
| Dice "al día" y hay versión nueva | La comprobación fallaba (TLS) y el mensaje decía "ya tienes la última". Log `update check: instalada=… ultima=… hay_nueva=…` |
| "argar como" en vez de "Descargar como copia" | Al añadir botones el diálogo no se ensanchaba: recalcular el ancho mínimo por botón |
| "Fallo en la descarga" sin motivo | Mostrar el motivo real y comprobar antes que el destino se puede escribir |
| La ventana arranca cortando la tercera fila | Alto por defecto medido (~741 px para 3 filas al 80 %, no 680) y "Restablecer tamaño" en Ajustes |
| Ajustes recorta la última fila | La tarjeta pedía 442 px donde había ~406: `QScrollArea` con `setWidgetResizable(True)` |

Los de la versión Kivy (rama `master`) están en la skill `kivy-expert`.
