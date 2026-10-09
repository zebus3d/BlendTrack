# Guía de Blender Manager

Esta carpeta explica **cómo está montado el programa** para que puedas
entenderlo y modificarlo a mano con el tiempo. Está escrita pensando en alguien
que está aprendiendo Python y Qt, así que empieza por lo básico y va subiendo.

## Por dónde empezar

1. **[00 - Empieza aquí](00-empieza-aqui.md)** — qué hace la aplicación, cómo se
   ejecuta y dónde está el archivo principal.
2. **[01 - Arquitectura](01-arquitectura.md)** — cómo se reparte el código por
   capas (modelo, servicios, interfaz) y cómo viaja la información.
3. **[02 - Qt](02-qt.md)** — los conceptos de Qt Widgets que usa el proyecto y
   dónde ver cada uno en el código.
4. **[03 - Diseño de la interfaz](03-diseno-ui-ux.md)** — los colores, los
   espacios y las reglas visuales, para que todo siga teniendo el mismo aspecto.
5. **[04 - Cómo añadir algo](04-como-anadir.md)** — ejemplo real y paso a paso:
   así se añadió el canal de ramas experimentales.

## Resumen en una imagen

```
                 ┌──────────────────────────────────────────┐
   EL USUARIO →  │  main.py  (arranca Qt y carga el QSS)     │
                 └────────────────────┬─────────────────────┘
                                      │
                 ┌────────────────────▼─────────────────────┐
   LO QUE VES →  │  ui/qss.py   +   ui/widgets/             │
                 │  (aspecto)       (comportamiento)         │
                 └────────────────────┬─────────────────────┘
                                      │ pide datos / acciones
                 ┌────────────────────▼─────────────────────┐
   LA LÓGICA →   │  services/  (api, forks, downloader,      │
                 │  installed, settings, updater... ~25)     │
                 └────────────────────┬─────────────────────┘
                                      │ usa
                 ┌────────────────────▼─────────────────────┐
   LOS DATOS →   │  model/build.py  (Build, InstalledBuild)  │
                 └──────────────────────────────────────────┘
```

La regla de oro del proyecto: **el QSS describe el aspecto, la ventana
(`ui/widgets/main_window.py` y sus mixins: `build_lists.py`, `downloads.py`,
`updates.py`, `settings_view.py`, `folder_library.py`) decide qué hacer, y los
servicios hacen el trabajo pesado**. Si mantienes esa separación, todo seguirá
siendo fácil de entender.

> Nota histórica: este proyecto nació con **Kivy** y se portó a Qt Widgets
> (PySide6) porque Kivy necesita OpenGL y eso hacía depender el binario del
> Mesa de cada distro (ver `AGENTS.md`). Kivy solo vive ya en el historial de
> git; cuando el resto de la guía lo menciona es para contar por qué algo se
> hace de una manera y no de otra, no porque quede código suyo.
> De aquella época quedan los `ui/widgets/` (misma idea, widgets de Qt) y nada
> más: los `.kv` se sustituyeron por `ui/qss.py`.
