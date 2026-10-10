"""Los sonidos de la galería: el "tic" al pasar de carta y el "clac" al elegir.

**No se empaqueta ningún fichero de sonido**: se sintetizan aquí, con
matemáticas, así que no hay licencia de nadie que respetar y no ocupan nada en
el binario. Se generan una vez como WAV en la caché. Son dos:

- **tick**: al pasar cada carta por el centro. Corto y seco, porque al girar la
  rueda con fuerza suenan muchos seguidos.
- **lock**: cuando una carta se queda en el centro. Rudo y con pegada, como el
  de los menús de los shooters clásicos: un golpe grave que cae de tono, un
  chasquido metálico (parciales inarmónicos, que es lo que suena a metal y no
  a nota), una ráfaga de ruido y un **segundo chasquido** 30 ms después, que es
  lo que da la sensación de que algo "encaja", como el cerrojo de un arma.
  Saturado y con un poco de *bitcrush* para el grano digital.

Tampoco hay Qt Multimedia: no viene en ``PySide6-Essentials`` y el spec lo
excluye (``QT_EXCLUDES``). Se reproduce con lo que trae cada sistema:

- Windows: ``winsound`` (biblioteca estándar de Python), asíncrono.
- macOS: ``afplay``.
- Linux: ``pw-play`` (PipeWire), ``paplay`` (PulseAudio) o ``aplay`` (ALSA),
  el primero que exista.

Los programas externos se lanzan con ``opener.clean_env()``: con el
``LD_LIBRARY_PATH`` del AppImage cargarían las bibliotecas del bundle y no
sonarían (ver ``AGENTS.md``, "el tercer gotcha").

No importa Qt.
"""

import math
import random
import shutil
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

from services import downloader, opener
from services.downloader import log

RATE = 44100
# Volumen de cada sonido (0..1, el pico tras normalizar). El tic, bajo: se oye
# muchas veces seguidas y no debe cansar. El clac, con fuerza: suena una vez.
VOLUME = {"tick": 0.16, "lock": 0.42}
# Va en el nombre del WAV: al cambiar el diseño, el viejo de la caché no se
# reutiliza.
SOUND_VERSION = 3

# Si suenan más seguidos que esto (una rueda girada con fuerza), se saltan:
# muchos procesos de audio a la vez se oyen como un zumbido.
MIN_GAP = 0.045

_last: dict = {}
_player: list = []


def _ms(milliseconds: float) -> int:
    return int(RATE * milliseconds / 1000)


def _decay(t: float, rate: float) -> float:
    return math.exp(-t * rate)


def _tick() -> list:
    """Un toque digital seco: un barrido agudo de 16 ms con su clic."""
    count = _ms(16)
    out = []
    phase = 0.0
    for n in range(count):
        t = n / RATE
        # La fase se acumula muestra a muestra: calcular ``sin(2π·f(t)·t)`` con
        # f variable desafina el barrido.
        phase += 2 * math.pi * (2600 + 1800 * t / (count / RATE)) / RATE
        tone = math.sin(phase) + 0.25 * math.sin(3 * phase)
        out.append(tone * min(1.0, t / 0.0008) * _decay(t, 260))
    out[0] += 0.6   # el borde del clic, que es lo que lo hace "seco"
    return out


def _click(count: int, rng: random.Random, partials) -> list:
    """Un chasquido metálico: parciales inarmónicos y ruido, que se apagan ya."""
    out = []
    for n in range(count):
        t = n / RATE
        metal = sum(math.sin(2 * math.pi * f * t) * _decay(t, k)
                    for f, k in partials)
        noise = (rng.random() * 2 - 1) * _decay(t, 420)
        out.append(0.55 * metal + 0.9 * noise)
    return out


def _lock() -> list:
    """El "clac" de selección (ver el docstring del módulo)."""
    rng = random.Random(3)
    total = _ms(130)
    mix = [0.0] * total
    # 1. El golpe: una senoide que cae de 150 a 55 Hz (la pegada en el pecho).
    phase = 0.0
    for n in range(_ms(90)):
        t = n / RATE
        phase += 2 * math.pi * (55 + 95 * _decay(t, 45)) / RATE
        mix[n] += 1.1 * math.sin(phase) * min(1.0, t / 0.0015) * _decay(t, 38)
    # 2. El chasquido metálico del primer impacto.
    first = _click(_ms(45), rng, ((1840, 90), (2730, 120), (4110, 150), (5390, 190)))
    for n, value in enumerate(first):
        mix[n] += value
    # 3. El segundo chasquido, más agudo y flojo: el "encaje".
    second = _click(_ms(35), rng, ((2310, 130), (3570, 170), (6120, 230)))
    offset = _ms(30)
    for n, value in enumerate(second):
        mix[offset + n] += 0.6 * value
    # Saturación (``tanh``): redondea los picos y le da cuerpo "rudo".
    mix = [math.tanh(2.2 * v) for v in mix]
    # *Bitcrush* suave: 5 bits de resolución y cada muestra repetida dos veces
    # (la mitad de frecuencia de muestreo). Es el grano de los samplers viejos.
    levels = 16
    crushed = []
    for n in range(0, total, 2):
        value = round(mix[n] * levels) / levels
        crushed.extend((value, value))
    return crushed[:total]


_RECIPES = {"tick": _tick, "lock": _lock}


def samples(name: str) -> list:
    """Las muestras de un sonido en enteros de 16 bits, normalizadas a su volumen."""
    mix = _RECIPES[name]()
    peak = max(1e-9, max(abs(v) for v in mix))
    # El final, a cero con un fundido de 3 ms: cortar a pelo da un chasquido.
    fade = _ms(3)
    for n in range(fade):
        mix[-1 - n] *= n / fade
    return [int(32767 * VOLUME[name] * v / peak) for v in mix]


def path_for(name: str) -> Path:
    """El WAV del sonido en la caché (se escribe la primera vez)."""
    path = downloader.cache_dir() / "sounds" / f"{name}-v{SOUND_VERSION}.wav"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        data = samples(name)
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(RATE)
            out.writeframes(struct.pack(f"<{len(data)}h", *data))
    return path


def _linux_player() -> list | None:
    for name, args in (("pw-play", []), ("paplay", []), ("aplay", ["-q"])):
        found = shutil.which(name)
        if found:
            return [found, *args]
    return None


def play(name: str) -> None:
    """Suena ``name`` sin esperar a que acabe. Nunca falla hacia fuera."""
    now = time.monotonic()
    if now - _last.get(name, 0.0) < MIN_GAP:
        return
    _last[name] = now
    try:
        path = str(path_for(name))
        if sys.platform.startswith("win"):
            import winsound

            # Windows solo reproduce un sonido a la vez con ``winsound``: el
            # clac corta al tic que estuviera sonando, que es lo que se quiere.
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC
                               | winsound.SND_NODEFAULT)
            return
        if not _player:
            _player.append(["/usr/bin/afplay"] if sys.platform == "darwin"
                           else _linux_player())
        if _player[0] is None:
            return
        subprocess.Popen([*_player[0], path], env=opener.clean_env(),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except (OSError, RuntimeError) as error:
        log(f"sound: {error}")


def play_tick() -> None:
    play("tick")


def play_lock() -> None:
    play("lock")
