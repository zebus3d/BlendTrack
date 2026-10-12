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

| Constante | Color | Para qué |
|---|---|---|
| `BG` | `#1D1D1D` | Fondo de la ventana (lo más oscuro) |
| `FIELD` | `#171717` | Campos de texto (hundidos) |
| `CARD_DIM` / `CARD_DIM_ALT` | oscuros | Tarjeta de la tienda que no tienes |
| `SURFACE` | `#303030` | Paneles, tarjetas instaladas, barra lateral |
| `CHROME` | `#303030` | Cabecera y pie |
| `SURFACE_ALT` | `#3D3D3D` | Resaltado al pasar el ratón |
| `BUTTON` | `#585858` | Botones neutros (gris de pestaña de Blender) |
| `TEXT` | `#E6E6E6` | Texto principal |
| `MUTED` | `#989898` | Texto secundario/apagado |
| `ACCENT` | `#5085B1` | Azul de selección/acción principal |
| `ACCENT_DARK` | `#3F6F96` | Azul pulsado/hover |
| `WARNING` | `#FFAF23` | Naranja (etiqueta LTS) |
| `DANGER` | `#B84A4A` | Rojo (desinstalar) |
| `SUCCESS_TEXT` | `#6FCF7A` | Verde claro para texto "Instalada" |
| `INFO_TEXT` | `#7AA7E0` | Azul claro para etiquetas de canal |

> Importante: los colores de texto llevan el sufijo `_TEXT` porque son más
> claros que los de fondo o de borde del mismo tono (`INFO_DISC`, `ACCENT`):
> un verde o un azul pensados para rellenar se leen mal como texto sobre una
> tarjeta.

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
