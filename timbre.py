#!/usr/bin/env python3
"""
timbre.py
=========

Selección automática de instrumento por SIMILITUD TÍMBRICA ("paso de ingeniero
de sonido"): en vez de asignar siempre el mismo instrumento a cada stem, se
escucha el audio separado y se elige el instrumento del banco de Smash Remix
que más se le parece.

Cómo funciona
-------------
1. Se extraen los samples reales del .sf2 del juego y se calcula una "huella
   tímbrica" de cada instrumento (MFCC + brillo + ruido/distorsión + ataque).
2. Se calcula la misma huella para el stem separado (guitarra, bajo, etc.).
3. Se compara SOLO contra los instrumentos de la MISMA FAMILIA (una guitarra
   compite contra guitarras). Esto evita resultados espectralmente parecidos
   pero musicalmente absurdos (p.ej. guitarra distorsionada -> White Noise).
4. Se devuelve el ranking; el mejor va a la voz grave y el segundo a la aguda,
   para que las dos sub-pistas no dupliquen el mismo timbre.

Limitación conocida
-------------------
El .sf2 disponible solo trae samples de los instrumentos 1-42 (los del ROM
original). Los 43-70 que agrega Smash Remix viven como .aifc con compresión
VADPCM de N64, que no es legible con herramientas estándar. Por eso la
selección automática solo elige entre instrumentos con sample disponible.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

# Frecuencia de muestreo común para comparar stem y samples del sf2.
SR = 22050
# Solo se analizan los primeros N segundos del stem (suficiente y mucho más rápido).
MAX_STEM_SECONDS = 60

# Umbral de confianza: si el mejor candidato queda más lejos que esto, se
# considera que el stem NO se parece a nada del banco y se respeta el
# instrumento por defecto en vez de forzar una elección mala.
#
# Calibrado con medidas reales: en stems donde la familia sí contiene el
# instrumento correcto (bajo, guitarra) las distancias caen en 0.5-1.1; cuando
# el instrumento adecuado no está disponible (voz -> los coros son 44/45/67,
# sin sample; piano -> el Acoustic Grand es el 52) se disparan a 3-28. El corte
# en 2.0 cae en el hueco natural entre ambos regímenes.
AUTO_MAX_DISTANCE = 2.0

# Rutas donde buscar el soundfont del juego (la primera que exista se usa).
# Primero la copia local del proyecto, para que funcione de forma autónoma sin
# depender de que el repositorio de Smash Remix esté clonado al lado.
_HERE = Path(__file__).resolve().parent
SF2_SEARCH_PATHS = [
    _HERE / "sf2" / "Smash64MidiInstruments.sf2",
    _HERE / "Smash64MidiInstruments.sf2",
    Path("../smashremix/src/music/sf2/Smash64MidiInstruments.sf2"),
]

# ----------------------------------------------------------------------
# Familias de candidatos por stem. Acotar por familia es lo que evita
# elecciones absurdas. Los números son programas del banco Smash Remix.
# (Solo se usarán los que tengan sample legible en el .sf2.)
# ----------------------------------------------------------------------
INSTRUMENT_FAMILIES = {
    "guitar": [38, 39, 41, 42],              # Nylon, Muted, Overdriven, Distortion
    "bass":   [11, 12, 13, 24, 32],          # Slap, Synth, Electric, Picked, Acoustic
    "vocals": [15, 7, 5, 1, 16],             # Choir Aahs, Strings, Brass, Flute, Pan Flute
    "piano":  [8, 26, 2, 10],                # Electric Piano, Bass-S.Chord-Piano, Organ, Glocken
    "other":  [6, 4, 19, 20, 3, 22, 34, 30], # Lead Synth, Synth Wave, NES waves, Tuba, Hit, Trumpet, Trombone
}


def find_sf2() -> Path | None:
    """Devuelve la ruta al .sf2 del juego, o None si no se encuentra."""
    for p in SF2_SEARCH_PATHS:
        if p.exists():
            return p
    return None


def _features(y: np.ndarray, sr: int) -> np.ndarray:
    """
    Huella tímbrica de una señal: MFCC (forma del espectro) + descriptores
    interpretables (brillo, ruido/distorsión, extensión, cruces por cero).
    """
    import librosa

    if sr != SR:
        y = librosa.resample(y, orig_sr=sr, target_sr=SR)
    if y.size < 512:
        raise ValueError("señal demasiado corta para analizar")
    y = librosa.util.normalize(y)

    mfcc = librosa.feature.mfcc(y=y, sr=SR, n_mfcc=13).mean(axis=1)
    centroid = librosa.feature.spectral_centroid(y=y, sr=SR).mean()   # brillo
    flatness = librosa.feature.spectral_flatness(y=y).mean()          # ruido/distorsión
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=SR).mean()     # extensión
    zcr = librosa.feature.zero_crossing_rate(y).mean()                # aspereza
    return np.concatenate([mfcc, [centroid, flatness, rolloff, zcr]]).astype(np.float64)


def _preset_audio(sf2, program: int) -> tuple[np.ndarray, int] | None:
    """Extrae el sample más largo (más representativo) del preset dado."""
    presets = [p for p in sf2.presets if getattr(p, "preset", None) == program]
    if not presets:
        return None
    best = None
    for inst in presets[0].instruments:
        if inst is None:
            continue
        for bag in inst.bags:
            smp = getattr(bag, "sample", None)
            if smp is None or not getattr(smp, "raw_sample_data", None):
                continue
            if best is None or smp.duration > best.duration:
                best = smp
    if best is None:
        return None
    pcm = np.frombuffer(best.raw_sample_data, dtype="<i2").astype(np.float32) / 32768.0
    return pcm, best.sample_rate


def build_bank_fingerprints(sf2_path: Path, programs: list[int]) -> dict[int, np.ndarray]:
    """
    Calcula (con caché en disco) la huella tímbrica de cada instrumento pedido.
    La caché se invalida sola si cambia el .sf2 (se indexa por tamaño y mtime).
    """
    from sf2utils.sf2parse import Sf2File

    stat = sf2_path.stat()
    cache_file = Path(__file__).resolve().parent / ".timbre_cache.json"
    cache_key = f"{sf2_path.name}:{stat.st_size}:{int(stat.st_mtime)}"

    cache: dict = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text())
        except Exception:  # noqa: BLE001  (caché corrupta: se regenera)
            cache = {}
    if cache.get("key") == cache_key:
        stored = {int(k): np.array(v) for k, v in cache.get("fingerprints", {}).items()}
        if all(p in stored for p in programs):
            return {p: stored[p] for p in programs}

    fingerprints: dict[int, np.ndarray] = {}
    with open(sf2_path, "rb") as fh:
        sf2 = Sf2File(fh)
        for prog in programs:
            audio = _preset_audio(sf2, prog)
            if audio is None:
                continue
            try:
                fingerprints[prog] = _features(audio[0], audio[1])
            except Exception:  # noqa: BLE001  (sample inservible: se omite)
                continue

    cache_file.write_text(json.dumps({
        "key": cache_key,
        "fingerprints": {str(k): v.tolist() for k, v in fingerprints.items()},
    }))
    return fingerprints


def stem_energy_db(wav_path: Path) -> float:
    """
    Nivel del stem en dBFS, medido como el percentil 95 de la energía RMS por
    ventana (no la media).

    Se usa un percentil alto a propósito: un stem puede estar en silencio la
    mayor parte de la canción y aun así ser importante cuando entra. La
    pregunta correcta es "¿llega a sonar?", no "¿suena todo el rato?". Con la
    media, un instrumento que solo aparece en el estribillo se descartaría.

    Se mide CADA CANAL por separado y se toma el más fuerte, en vez de sumar a
    mono: si los canales tienen componentes fuera de fase, la suma los cancela
    parcialmente y el stem parecería más silencioso de lo que es.
    """
    import librosa

    y, _ = librosa.load(str(wav_path), sr=SR, mono=False)  # canción completa
    if y.size == 0:
        return float("-inf")
    channels = y if y.ndim > 1 else y[np.newaxis, :]

    best = 0.0
    for channel in channels:
        rms = librosa.feature.rms(y=np.ascontiguousarray(channel)).flatten()
        if rms.size:
            best = max(best, float(np.percentile(rms, 95)))
    if best <= 0:
        return float("-inf")
    return float(20.0 * np.log10(best))


def stem_fingerprint(wav_path: Path) -> np.ndarray:
    """Huella tímbrica del stem separado."""
    import librosa

    y, sr = librosa.load(str(wav_path), sr=SR, mono=True, duration=MAX_STEM_SECONDS)
    if y.size == 0 or not np.any(y):
        raise ValueError("stem vacío o en silencio")
    return _features(y, sr)


def rank_candidates(stem_name: str, wav_path: Path, sf2_path: Path) -> list[tuple[int, float]]:
    """
    Devuelve los instrumentos candidatos de la familia del stem, ordenados del
    más parecido al menos parecido: [(programa, distancia), ...].
    Lista vacía si el stem no tiene familia definida o no hay samples.
    """
    family = INSTRUMENT_FAMILIES.get(stem_name)
    if not family:
        return []

    bank = build_bank_fingerprints(sf2_path, family)
    if not bank:
        return []

    target = stem_fingerprint(wav_path)
    progs = sorted(bank)
    matrix = np.vstack([bank[p] for p in progs])

    # Se normaliza cada dimensión (z-score) con la estadística de los candidatos
    # para que ninguna característica de escala grande (p.ej. rolloff en Hz)
    # domine la distancia.
    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    std[std == 0] = 1.0
    matrix_z = (matrix - mean) / std
    target_z = (target - mean) / std

    # Pesos por bloque: los 13 MFCC describen la forma global del espectro, y
    # los 4 descriptores (brillo, ruido/distorsión, extensión, aspereza) son los
    # musicalmente decisivos. Sin pesar, los MFCC ganan solo por ser más
    # numerosos y tapan la diferencia entre, p.ej., limpia y distorsionada.
    # Se escala cada bloque para que ambos aporten lo mismo a la distancia.
    weights = np.concatenate([
        np.full(13, 0.5 / np.sqrt(13)),   # bloque MFCC
        np.full(4, 0.5 / np.sqrt(4)),     # bloque descriptores
    ])
    distances = np.linalg.norm((matrix_z - target_z) * weights, axis=1)
    return sorted(zip(progs, distances.tolist()), key=lambda t: t[1])
