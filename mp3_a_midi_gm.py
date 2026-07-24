#!/usr/bin/env python3
"""
mp3_a_midi_gm.py
=================

Separa los instrumentos de un archivo de audio y genera un único archivo
.midi multipista (máx. 16 pistas) mapeado al banco de instrumentos de
Smash Remix (Super Smash Bros. 64), NO a General MIDI.

Acepta cualquier formato que lea ffmpeg (.wav, .mp3, .flac, .m4a, ...).
Se recomienda .wav o .flac: al ser sin pérdida, la separación de fuentes
y la transcripción salen mejor que partiendo de un .mp3 comprimido.

Pipeline:
  1) Separación de fuentes -> Demucs (Meta/Facebook Research)
     Modelo por defecto: htdemucs_6s -> vocals, drums, bass, guitar, piano, other
     (si se usa htdemucs / htdemucs_ft, se obtienen 4 stems: vocals, drums, bass, other)
  2) Transcripción audio -> notas MIDI por cada stem -> basic-pitch (Spotify)
  3) Ensamblado en un único .mid con pretty_midi, asignando a cada stem el
     programa del banco SmashRemix (índice 1-70; batería = programa 18).
     Ver el mapeo y las reglas duras en SMASH_STEM_MAP más abajo.

------------------------------------------------------------------------
INSTALACIÓN
------------------------------------------------------------------------
Requiere Python 3.9–3.11 y ffmpeg instalado en el sistema.

    # Linux/Mac
    sudo apt-get install ffmpeg      # o brew install ffmpeg

    pip install -r requirements.txt

(ver requirements.txt adjunto). La primera ejecución de Demucs descarga
los pesos del modelo (~80-300 MB según el modelo elegido).

------------------------------------------------------------------------
USO
------------------------------------------------------------------------
    python mp3_a_midi_gm.py cancion.wav
    python mp3_a_midi_gm.py cancion.mp3 -o salida.mid --modelo htdemucs_6s
    python mp3_a_midi_gm.py cancion.wav --dispositivo cuda --auto-instrumentos

------------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ----------------------------------------------------------------------
# Dependencias pesadas: se importan de forma diferida (lazy) dentro de las
# funciones que las usan, para que --help funcione aunque falte instalar algo.
# ----------------------------------------------------------------------

MAX_TRACKS = 16

# ----------------------------------------------------------------------
# Banco de instrumentos de Smash Remix (Super Smash Bros. 64), NO es
# General MIDI. Son 70 entradas y el número de programa es un índice
# DIRECTO a ese banco: el byte del Program Change (Cn prog) = índice 1-70.
#
# Reglas duras documentadas en el readme del repositorio de Smash Remix:
#   - Todo programa debe estar entre 1 y 70. Un programa fuera de rango
#     (0, o 71+) CORTA TODO EL AUDIO del juego al entrar ese canal.
#   - El programa 18 es la percusión (Main Percussion). No hay canal de
#     batería reservado como en GM: el canal es indiferente, solo manda
#     el número de programa.
#   - Máx. 16 pistas (una por canal MIDI); el resto se trunca al convertir.
#
# En pretty_midi, Instrument.program se escribe TAL CUAL como el byte del
# Program Change, así que aquí guardamos el índice del banco directamente
# (sin el "-1" que se usaría con General MIDI).
# ----------------------------------------------------------------------
SMASH_MIN_PROGRAM = 1
SMASH_MAX_PROGRAM = 70
SMASH_DRUM_PROGRAM = 18  # Main Percussion (obligatorio para la batería)

# Mapeo stem -> (programa banco SmashRemix 1-70, nombre de pista, es_percusion)
SMASH_STEM_MAP = {
    "vocals":  (15, "Vocals (Choir Aahs)",         False),  # 15 = Choir Aahs
    "piano":   (52, "Piano (Acoustic Grand)",      False),  # 52 = Acoustic Grand Piano
    "guitar":  (38, "Guitar (Nylon)",              False),  # 38 = Nylon Guitar
    "bass":    (13, "Bass (Electric)",             False),  # 13 = Electric Bass
    "drums":   (SMASH_DRUM_PROGRAM, "Drums (Main Percussion)", True),  # 18 = Main Percussion
    "other":   (6,  "Other (Lead Synth)",          False),  # 6 = Lead Synth
}

# ----------------------------------------------------------------------
# Paleta COMPLETA del banco de Smash Remix: byte de programa (1-70) ->
# (nombre real del instrumento, nombre GM que muestra el editor).
# Fuente: tabla del readme de Smash Remix + tabla cruzada GM->Smash. Sirve
# para elegir a mano el instrumento de cada stem por canción (ver flags
# --voz/--bajo/--guitarra/--piano/--otros y --listar-instrumentos).
# ----------------------------------------------------------------------
SMASH_BANK = {
    1:  ("Flute",                 "Bright Acoustic"),
    2:  ("Organ",                 "Electric Grand"),
    3:  ("Synth Tuba",            "Honky-Tonk"),
    4:  ("Synth Wave",            "Electric Piano 1"),
    5:  ("Brass",                 "Electric Piano 2"),
    6:  ("Lead Synth",            "Harpsichord"),
    7:  ("Strings",               "Clavinet"),
    8:  ("Electric Piano",        "Celesta"),
    9:  ("Kalimba",               "Glockenspiel"),
    10: ("Glockenspiel",          "Music Box"),
    11: ("Slap Bass",             "Vibraphone"),
    12: ("Synth Bass",            "Marimba"),
    13: ("Electric Bass",         "Xylophone"),
    14: ("Banjo",                 "Tubular Bells"),
    15: ("Choir Aahs",            "Dulcimer"),
    16: ("Pan Flute",             "Drawbar Organ"),
    17: ("Timpani",               "Percussive Organ"),
    18: ("Main Percussion",       "Rock Organ"),
    19: ("Square Wave (NES)",     "Church Organ"),
    20: ("Triangle (NES)",        "Reed Organ"),
    21: ("White Noise (NES)",     "Accordion"),
    22: ("Orchestral Hit",        "Harmonica"),
    23: ("Drum Roll",             "Tango Accordion"),
    24: ("Picked Bass-Clav-Organ","Nylon String Guitar"),
    25: ("TR-808 Synth Drum",     "Steel String Guitar"),
    26: ("Bass-S.Chord-Piano",    "Electric Jazz Guitar"),
    27: ("Drums+Tubular Bells",   "Electric Clean Guitar"),
    28: ("Pan Flute 2",           "Electric Muted Guitar"),
    29: ("Synth Accordion",       "Overdriven Guitar"),
    30: ("Trombone",              "Distortion Guitar"),
    31: ("Drum w/ Cowbell",       "Guitar Harmonics"),
    32: ("Acoustic Bass",         "Acoustic Bass"),
    33: ("Steel Drums",           "Electric Bass (finger)"),
    34: ("Trumpet",               "Electric Bass (pick)"),
    35: ("Accordion",             "Fretless Bass"),
    36: ("Bassoon",               "Slap Bass 1"),
    37: ("Clarinet",              "Slap Bass 2"),
    38: ("Nylon Guitar",          "Synth Bass 1"),
    39: ("Muted Guitar",          "Synth Bass 2"),
    40: ("Muted Trumpet",         "Violin"),
    41: ("Overdriven Guitar",     "Viola"),
    42: ("Distortion Guitar",     "Cello"),
    43: ("Rock Organ",            "Contrabass"),
    44: ("Choir Ahhs 2",          "Tremolo Strings"),
    45: ("Choir Oohs",            "Pizzicato Strings"),
    46: ("Slap Bass (Alt)",       "Orchestral Harp"),
    47: ("Church Organ",          "Timpani"),
    48: ("Steel Drum 2",          "String Ensemble 1"),
    49: ("Distortion Guitar 2",   "String Ensemble 2"),
    50: ("Tenor Sax",             "SynthStrings 1"),
    51: ("Overdriven Guitar 2",   "SynthStrings 2"),
    52: ("Acoustic Grand Piano",  "Choir Aahs"),
    53: ("Slap Bass 1",           "Voice Oohs"),
    54: ("Orchestra Hit",         "Synth Voice"),
    55: ("Synth (Alt)",           "Orchestra Hit"),
    56: ("Missing NES Wave",      "Trumpet"),
    57: ("Nylon Guitar (Alt)",    "Trombone"),
    58: ("Sawtooth (K64)",        "Tuba"),
    59: ("Shogo Sakai Slide",     "Muted Trumpet"),
    60: ("OOT Acoustic",          "French Horn"),
    61: ("Pizzicato (FFXI)",      "Brass Section"),
    62: ("Shamisen",              "SynthBrass 1"),
    63: ("DK Rap",                "SynthBrass 2"),
    64: ("Roll",                  "Soprano Sax"),
    65: ("Yoshis",                "Alto Sax"),
    66: ("Marimba",               "Tenor Sax"),
    67: ("DF Chants",             "Baritone Sax"),
    68: ("Monkeys",               "Oboe"),
    69: ("Sine Wave",             "English Horn"),
    70: ("Harp",                  "Bassoon"),
}

# Índice normalizado (nombre_real -> byte) para buscar por nombre en la CLI.
def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


SMASH_NAME_TO_PROGRAM = {_norm(name): prog for prog, (name, _gm) in SMASH_BANK.items()}


def resolve_instrument(value: str) -> int:
    """
    Convierte lo que el usuario pasó por CLI (un número 1-70 o el nombre de un
    instrumento del banco) en el byte de programa correspondiente. Aborta con
    un mensaje claro si no es válido.
    """
    value = value.strip()
    if value.isdigit():
        prog = int(value)
        if not (SMASH_MIN_PROGRAM <= prog <= SMASH_MAX_PROGRAM):
            sys.exit(f"ERROR: programa {prog} fuera de rango 1-70. Usa --listar-instrumentos.")
        return prog
    prog = SMASH_NAME_TO_PROGRAM.get(_norm(value))
    if prog is None:
        sys.exit(
            f"ERROR: instrumento '{value}' no existe en el banco. "
            "Usa --listar-instrumentos para ver los nombres válidos."
        )
    return prog


def print_instrument_bank() -> None:
    """Imprime la paleta completa del banco de Smash Remix y termina."""
    print("Banco de instrumentos de Smash Remix (byte -> instrumento real  [nombre GM del editor])")
    print("=" * 78)
    for prog in sorted(SMASH_BANK):
        real, gm = SMASH_BANK[prog]
        marca = "  <- percusión (batería)" if prog == SMASH_DRUM_PROGRAM else ""
        print(f"  {prog:>2}  {real:<26} [{gm}]{marca}")
    print("\nUso: --voz / --bajo / --guitarra / --piano / --otros  <número o nombre>")


# Instrumento alternativo para las sub-pistas AGUDAS cuando se divide un stem
# por registro. Evita que las dos voces dupliquen el mismo timbre (que "marea"
# al escuchar) y suena más a un arreglo real: grave y agudo con timbres
# complementarios. Estos no necesitan sample en el .sf2, así que pueden usar
# cualquier instrumento del banco 1-70.
REGISTER_ALT_DEFAULT = {
    "vocals": 44,  # Choir Aahs (grave) -> Choir Ahhs 2 (agudo)
    "piano":  8,   # Acoustic Grand      -> Electric Piano
    "guitar": 41,  # Nylon Guitar        -> Overdriven Guitar
    "bass":   11,  # Electric Bass       -> Slap Bass
    "other":  19,  # Lead Synth          -> Square Wave (NES)
}

# Orden de prioridad si algún día hay más de 16 stems (robustez, no debería
# ocurrir con Demucs, que da máx. 6 stems, pero se deja preparado).
STEM_PRIORITY = ["vocals", "drums", "bass", "piano", "guitar", "other"]

# Stems polifónicos que conviene dividir en sub-voces por registro de altura
# para que cada pista quede más limpia y fácil de distinguir/editar.
# Batería y bajo se dejan siempre como 1 sola pista (percusión y línea de bajo
# suelen ser más claras sin dividir).
SPLIT_ELIGIBLE_STEMS = {"vocals", "piano", "guitar", "other"}

# Stems que deben quedar monofónicos (una sola nota a la vez). Una línea de
# bajo real es monofónica; forzarlo limpia mucho el resultado y ahorra voces
# de la N64, que tiene polifonía limitada.
MONOPHONIC_STEMS = {"bass"}

# Perfiles de densidad de transcripción.
#
# Filosofía: preservar la ORIGINALIDAD de la canción. En vez de subir los
# umbrales de basic-pitch (que tira notas reales a ciegas y deja un sonido
# pobre, "8-bit"), dejamos que basic-pitch capture (casi) toda la canción y
# reducimos densidad con post-filtros QUIRÚRGICOS: se conservan las notas más
# presentes (mayor velocity) y solo se descarta ruido y el exceso de polifonía
# que la N64 no puede reproducir.
#   - onset/frame_threshold, minimum_note_length -> parámetros de basic-pitch.
#     Se dejan cerca del default (0.5 / 0.3) para no perder fidelidad.
#   - min_note_seconds -> post-filtro: descarta notas más cortas que esto (ruido).
#   - max_voices -> post-filtro: tope de notas simultáneas por stem
#     (0 = sin tope). Conserva las de mayor velocity.
DENSITY_PROFILES = {
    # alta = máxima fidelidad: basic-pitch por defecto, casi sin poda.
    "alta":  dict(onset_threshold=0.5,  frame_threshold=0.3, minimum_note_length=100.0,
                  min_note_seconds=0.0,  max_voices=0),
    # media (por defecto) = fiel con poda suave: mismos umbrales, quita ruido y
    # limita la polifonía a un nivel que la N64 sí reproduce.
    "media": dict(onset_threshold=0.5,  frame_threshold=0.3, minimum_note_length=120.0,
                  min_note_seconds=0.04, max_voices=6),
    # baja = para canciones MUY densas: recorte más fuerte (puede sonar pobre).
    "baja":  dict(onset_threshold=0.6,  frame_threshold=0.4, minimum_note_length=180.0,
                  min_note_seconds=0.07, max_voices=4),
}

# La batería siempre se transcribe con umbrales de fidelidad máxima: ya se
# detecta pobremente (es percusión no afinada) y subir umbrales la borra por
# completo. La densidad elegida NO se aplica a la batería.
DRUMS_TRANSCRIBE_PROFILE = "alta"


def log(msg: str) -> None:
    print(f"[mp3_a_midi_gm] {msg}", flush=True)


def check_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        sys.exit(
            "ERROR: no se encontró 'ffmpeg' en el sistema. "
            "Instálalo (p. ej. 'sudo apt-get install ffmpeg' o 'brew install ffmpeg') "
            "y vuelve a intentar."
        )


def run_demucs(input_mp3: Path, work_dir: Path, model: str, device: str) -> dict[str, Path]:
    """
    Ejecuta Demucs vía su CLI (más estable que la API interna entre versiones)
    y devuelve un diccionario {nombre_stem: ruta_wav}.
    """
    log(f"Separando instrumentos con Demucs (modelo='{model}', dispositivo='{device}')...")

    cmd = [
        sys.executable, "-m", "demucs",
        "-n", model,
        "-d", device,
        "-o", str(work_dir),
        str(input_mp3),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(result.stdout)
        log(result.stderr)
        sys.exit("ERROR: Demucs falló al separar el audio (ver log arriba).")

    stems_dir = work_dir / model / input_mp3.stem
    if not stems_dir.exists():
        sys.exit(f"ERROR: no se encontró la carpeta de stems esperada: {stems_dir}")

    stems = {wav.stem: wav for wav in stems_dir.glob("*.wav")}
    if not stems:
        sys.exit("ERROR: Demucs no generó ningún archivo .wav de salida.")

    log(f"Stems obtenidos: {', '.join(sorted(stems.keys()))}")
    return stems


# RoFormer vive en un venv aparte (venv-separator) porque audio-separator exige
# numpy>=2, incompatible con el numpy<2 que necesitan demucs/basic-pitch.
SEPARATOR_CLI = Path(__file__).resolve().parent / "venv-separator" / "bin" / "audio-separator"
ROFORMER_VOCAL_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"  # BS-Roformer Viperx


def run_roformer_plus_demucs(
    input_audio: Path, work_dir: Path, demucs_model: str, device: str
) -> dict[str, Path]:
    """
    Separación de mayor calidad en dos etapas:
      1) RoFormer (audio-separator, venv aislado) aísla la VOZ del resto,
         produciendo un stem 'Vocals' y otro 'Instrumental'.
      2) Demucs corre sobre el Instrumental (ya sin voz) para obtener los
         otros 5 stems (drums, bass, guitar, piano, other) más limpios, sin
         el sangrado de la voz.
    Devuelve {nombre_stem: ruta_wav} con la voz de RoFormer y el resto de Demucs.
    """
    log("Separando voz con RoFormer (audio-separator, venv aislado)...")
    if not SEPARATOR_CLI.exists():
        sys.exit(
            f"ERROR: no se encontró el separador RoFormer en {SEPARATOR_CLI}. "
            "Crea el venv aislado con audio-separator o usa --separador demucs."
        )

    ro_dir = work_dir / "roformer"
    ro_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(SEPARATOR_CLI), str(input_audio),
        "--model_filename", ROFORMER_VOCAL_MODEL,
        "--output_dir", str(ro_dir),
        "--output_format", "WAV",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(result.stdout)
        log(result.stderr)
        sys.exit("ERROR: RoFormer (audio-separator) falló al separar la voz.")

    vocals = next(iter(ro_dir.glob("*(Vocals)*.wav")), None)
    instrumental = next(iter(ro_dir.glob("*(Instrumental)*.wav")), None)
    if vocals is None or instrumental is None:
        sys.exit(f"ERROR: RoFormer no produjo Vocals/Instrumental en {ro_dir}")
    log(f"RoFormer OK: voz='{vocals.name}'")

    # Demucs sobre el instrumental (sin voz) para el resto de los stems.
    stems = run_demucs(instrumental, work_dir, demucs_model, device)
    stems.pop("vocals", None)   # descartamos la voz residual de Demucs
    stems["vocals"] = vocals    # usamos la voz limpia de RoFormer
    return stems


def apply_energy_gate(stems: dict[str, Path], threshold_db: float) -> dict[str, Path]:
    """
    Descarta los stems cuyo nivel quede más de `threshold_db` por debajo del
    stem más fuerte. Devuelve el diccionario filtrado.

    Ante cualquier error de medición se conserva el stem: es preferible una
    pista de más que perder música por un fallo del analizador.
    """
    import timbre

    log("Midiendo nivel de cada stem (gate de energía)...")
    levels: dict[str, float] = {}
    for name, wav_path in stems.items():
        try:
            levels[name] = timbre.stem_energy_db(wav_path)
        except Exception as exc:  # noqa: BLE001
            log(f"  AVISO: no se pudo medir '{name}' ({exc}); se conserva.")
            levels[name] = 0.0

    finite = [v for v in levels.values() if v != float("-inf")]
    if not finite:
        return stems
    loudest = max(finite)

    kept: dict[str, Path] = {}
    for name, wav_path in stems.items():
        rel = levels[name] - loudest
        if rel < threshold_db:
            log(f"  {name}: DESCARTADO — sin contenido real ({rel:.1f} dB bajo el más fuerte)")
        else:
            kept[name] = wav_path
            log(f"  {name}: {rel:.1f} dB")

    return kept if kept else stems


def transcribe_stem(wav_path: Path, profile: dict):
    """
    Transcribe un stem de audio a eventos de nota usando basic-pitch,
    aplicando los umbrales del perfil de densidad elegido.
    Devuelve un objeto pretty_midi.PrettyMIDI con (normalmente) un solo
    instrumento con las notas detectadas.
    """
    from basic_pitch.inference import predict
    from basic_pitch import ICASSP_2022_MODEL_PATH

    log(f"  Transcribiendo a MIDI: {wav_path.name}")
    _, midi_data, _ = predict(
        str(wav_path),
        ICASSP_2022_MODEL_PATH,
        onset_threshold=profile["onset_threshold"],
        frame_threshold=profile["frame_threshold"],
        minimum_note_length=profile["minimum_note_length"],
    )
    return midi_data


def split_notes_by_register(notes: list, n_voices: int) -> list[list]:
    """
    Divide una lista de notas MIDI en n_voices grupos según su altura (pitch),
    usando cuantiles de la distribución de pitches del propio stem. El grupo 0
    queda con las notas más graves y el último con las más agudas.

    Esto NO es una separación "inteligente" de voces por continuidad melódica,
    es una división por registro (como separar la mano izquierda/derecha en
    un piano); pero es suficiente para lograr pistas más legibles y es una
    técnica estándar cuando no se dispone de anotación de voces reales.
    """
    import numpy as np

    if n_voices <= 1 or not notes:
        return [notes]

    pitches = np.array([n.pitch for n in notes], dtype=float)
    edges = np.quantile(pitches, np.linspace(0, 1, n_voices + 1))

    voices: list[list] = [[] for _ in range(n_voices)]
    for note in notes:
        idx = n_voices - 1
        for i in range(n_voices):
            if note.pitch <= edges[i + 1]:
                idx = i
                break
        voices[idx].append(note)
    return voices


def filter_short_notes(notes: list, min_seconds: float) -> list:
    """Descarta notas más cortas que min_seconds (ruido de transcripción)."""
    if min_seconds <= 0:
        return notes
    return [n for n in notes if (n.end - n.start) >= min_seconds]


def make_monophonic(notes: list) -> list:
    """
    Reduce a monofónico: como máximo una nota sonando a la vez. Recorre las
    notas por tiempo de inicio; si una empieza mientras otra sigue sonando,
    trunca la anterior en ese punto. Ante inicios simultáneos, se prioriza la
    de mayor velocity (más presente en la mezcla).
    """
    if not notes:
        return notes
    ordered = sorted(notes, key=lambda n: (n.start, -n.velocity))
    result: list = []
    for note in ordered:
        if result and note.start < result[-1].end:
            result[-1].end = note.start  # trunca la previa hasta este inicio
        if note.end > note.start:
            result.append(note)
    return [n for n in result if n.end > n.start]


def cap_polyphony(notes: list, max_voices: int) -> list:
    """
    Limita la polifonía a max_voices notas simultáneas. Usa un barrido de
    eventos: cuando en un instante suenan más de max_voices notas, descarta
    las de menor velocity (las más débiles/menos audibles).
    """
    if max_voices <= 0 or len(notes) <= max_voices:
        return notes
    ordered = sorted(notes, key=lambda n: n.start)
    kept: list = []
    active: list = []  # notas conservadas que siguen sonando
    for note in ordered:
        active = [n for n in active if n.end > note.start]
        if len(active) < max_voices:
            kept.append(note)
            active.append(note)
        else:
            weakest = min(active, key=lambda n: n.velocity)
            if note.velocity > weakest.velocity:
                kept.remove(weakest)
                active.remove(weakest)
                kept.append(note)
                active.append(note)
            # si no supera a la más débil, se descarta esta nota
    return kept


def apply_density_filters(stem_name: str, notes: list, profile: dict) -> list:
    """Aplica los post-filtros de densidad a un stem melódico (no batería)."""
    notes = filter_short_notes(notes, profile["min_note_seconds"])
    if stem_name in MONOPHONIC_STEMS:
        notes = make_monophonic(notes)
    notes = cap_polyphony(notes, profile["max_voices"])
    return notes


def build_gm_tracks_for_stem(stem_name: str, midi_data, n_voices: int, profile: dict,
                             stem_map: dict, register_alt: dict | None = None) -> list:
    """
    Convierte las notas transcritas de un stem en una o varias pistas
    pretty_midi.Instrument con el programa GM correspondiente.

    - Batería: siempre 1 sola pista, remapeada al canal de percusión GM.
    - Bajo: siempre 1 sola pista (una línea de bajo dividida pierde sentido).
    - Voz / piano / guitarra / otros: se dividen en n_voices sub-pistas por
      registro de altura (si n_voices > 1) para mayor claridad.
    """
    import pretty_midi

    # Fallback para stems no mapeados: programa 52 (Acoustic Grand Piano),
    # dentro de rango 1-70 para no arriesgar el corte de audio del juego.
    program, base_name, is_drum = stem_map.get(
        stem_name, (52, f"{stem_name.title()} (Acoustic Grand)", False)
    )

    all_notes = [note for src in midi_data.instruments for note in src.notes]
    if not all_notes:
        return []

    # --- Batería: 1 pista, programa 18 (Main Percussion) ---
    # El canal es indiferente en SmashRemix; lo que manda es el programa 18,
    # así que NO usamos is_drum=True (que forzaría el canal 10 de GM). Solo
    # fijamos program=18 como un instrumento melódico más.
    if is_drum:
        instrument = pretty_midi.Instrument(program=program, is_drum=False, name=base_name)
        for note in all_notes:
            instrument.notes.append(
                pretty_midi.Note(
                    velocity=note.velocity,
                    pitch=remap_to_gm_drum(note.pitch),
                    start=note.start,
                    end=note.end,
                )
            )
        return [instrument]

    # --- Post-filtros de densidad (solo stems melódicos) ---
    n_before = len(all_notes)
    all_notes = apply_density_filters(stem_name, all_notes, profile)
    if len(all_notes) != n_before:
        log(f"  Densidad '{stem_name}': {n_before} -> {len(all_notes)} notas")
    if not all_notes:
        return []

    # --- Stems no elegibles para división (p.ej. bajo), o n_voices=1 ---
    if stem_name not in SPLIT_ELIGIBLE_STEMS or n_voices <= 1:
        instrument = pretty_midi.Instrument(program=program, is_drum=False, name=base_name)
        for note in all_notes:
            instrument.notes.append(
                pretty_midi.Note(velocity=note.velocity, pitch=note.pitch, start=note.start, end=note.end)
            )
        return [instrument]

    # --- División en sub-voces por registro ---
    voice_groups = split_notes_by_register(all_notes, n_voices)
    instruments = []
    for i, notes in enumerate(voice_groups):
        if not notes:
            continue
        # Voz 1 (grave) usa el instrumento principal; las agudas usan el
        # alternativo si hay uno, para no duplicar el mismo timbre.
        voice_program, voice_base = program, base_name
        if i > 0 and register_alt and stem_name in register_alt:
            voice_program, voice_base = register_alt[stem_name]
        lo = pretty_midi.note_number_to_name(min(n.pitch for n in notes))
        hi = pretty_midi.note_number_to_name(max(n.pitch for n in notes))
        name = f"{voice_base} - Voz {i + 1}/{n_voices} ({lo}-{hi})"
        instrument = pretty_midi.Instrument(program=voice_program, is_drum=False, name=name)
        for note in notes:
            instrument.notes.append(
                pretty_midi.Note(velocity=note.velocity, pitch=note.pitch, start=note.start, end=note.end)
            )
        instruments.append(instrument)
    return instruments


def remap_to_gm_drum(pitch: int) -> int:
    """
    Heurística simple para mapear alturas detectadas en el stem de batería
    a un conjunto pequeño de notas de percusión (convención GM):
      36 = Bass Drum 1, 38 = Acoustic Snare, 42 = Closed Hi-Hat, 49 = Crash Cymbal 1
    Se reparte el rango de pitches detectado en graves/medios/agudos.

    NOTA: en SmashRemix la batería es el programa 18 (Main Percussion) y el
    mapeo nota->sonido dentro de ese kit NO está documentado; puede no
    coincidir con GM. Es un punto a verificar en el juego (TODO).
    """
    if pitch < 45:
        return 36  # Bombo
    elif pitch < 60:
        return 38  # Caja
    elif pitch < 75:
        return 42  # Hi-hat cerrado
    else:
        return 49  # Platillo crash


def validate_smash_programs(tracks: list) -> None:
    """
    Verifica que TODA pista tenga un programa dentro del banco SmashRemix
    (1-70). Un programa fuera de rango corta todo el audio del juego, así
    que abortamos antes de escribir un .mid inservible.
    """
    for inst in tracks:
        if not (SMASH_MIN_PROGRAM <= inst.program <= SMASH_MAX_PROGRAM):
            sys.exit(
                f"ERROR: la pista '{inst.name}' tiene programa {inst.program}, "
                f"fuera del rango válido de SmashRemix ({SMASH_MIN_PROGRAM}-{SMASH_MAX_PROGRAM}). "
                "Un programa fuera de rango apaga TODO el audio del juego; se aborta."
            )


def assemble_midi(
    stem_midis: dict[str, "pretty_midi.PrettyMIDI"], output_path: Path,
    n_voices: int, profile: dict, stem_map: dict,
    register_alt: dict | None = None,
) -> None:
    import pretty_midi

    final_midi = pretty_midi.PrettyMIDI()

    # Se ordenan los stems por prioridad (batería y bajo primero: van a 1
    # sola pista siempre, así se reserva espacio para las sub-voces del resto)
    ordered_stems = [s for s in STEM_PRIORITY if s in stem_midis]
    ordered_stems += [s for s in stem_midis if s not in ordered_stems]

    all_tracks = []
    for stem_name in ordered_stems:
        tracks = build_gm_tracks_for_stem(stem_name, stem_midis[stem_name], n_voices, profile,
                                          stem_map, register_alt)
        if not tracks:
            log(f"  (omitido: '{stem_name}' no tiene notas detectadas)")
        all_tracks.extend(tracks)

    if len(all_tracks) > MAX_TRACKS:
        log(f"AVISO: se generaron {len(all_tracks)} pistas; se recortan a {MAX_TRACKS}.")
        all_tracks = all_tracks[:MAX_TRACKS]

    validate_smash_programs(all_tracks)

    final_midi.instruments.extend(all_tracks)

    if not final_midi.instruments:
        sys.exit("ERROR: no se generó ninguna pista con notas; no hay nada que guardar.")

    final_midi.write(str(output_path))
    log(f"MIDI final guardado en: {output_path}  ({len(final_midi.instruments)} pistas)")
    for inst in final_midi.instruments:
        log(f"  - {inst.name}  ({len(inst.notes)} notas)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Separa instrumentos de un MP3 y genera un MIDI multipista con sonidos GM."
    )
    parser.add_argument(
        "entrada", type=Path, nargs="?",
        help="Archivo de audio de entrada (.wav, .mp3, .flac, ... cualquiera que "
             "lea ffmpeg). Se recomienda .wav o .flac: al ser sin pérdida dan "
             "mejor separación y transcripción que un .mp3",
    )
    parser.add_argument(
        "-o", "--salida", type=Path, default=None,
        help="Ruta del .mid de salida (por defecto: <entrada>.mid)"
    )
    parser.add_argument(
        "--modelo", default="htdemucs_6s",
        choices=["htdemucs", "htdemucs_ft", "htdemucs_6s", "mdx_extra"],
        help="Modelo de Demucs. 'htdemucs_6s' separa 6 instrumentos "
             "(vocals, drums, bass, guitar, piano, other). Por defecto: htdemucs_6s",
    )
    parser.add_argument(
        "--dispositivo", default="cpu", choices=["cpu", "cuda"],
        help="Dispositivo para Demucs/basic-pitch (usar 'cuda' si tienes GPU NVIDIA)"
    )
    parser.add_argument(
        "--voces-por-stem", type=int, default=2, choices=[1, 2, 3],
        help="En cuántas sub-pistas por registro (agudo/medio/grave) dividir "
             "vocals/piano/guitar/other. Batería y bajo siempre quedan en 1 "
             "pista. Con el valor por defecto (2) se obtienen ~10 pistas; "
             "con 3 se pueden llegar a ~14 (máximo total: 16). Por defecto: 2",
    )
    parser.add_argument(
        "--densidad", default="alta", choices=list(DENSITY_PROFILES.keys()),
        help="Densidad de notas de la transcripción. 'alta' (por defecto) = máxima "
             "fidelidad al original. 'media' poda ruido y limita polifonía por stem. "
             "'baja' recorta fuerte (solo para canciones muy densas; puede sonar pobre)",
    )
    parser.add_argument(
        "--separador", default="roformer", choices=["demucs", "roformer"],
        help="Motor de separación de fuentes. 'roformer' (por defecto) = BS-RoFormer "
             "para la voz + Demucs sobre el instrumental para el resto (mejor calidad). "
             "'demucs' = htdemucs_6s solo (más rápido, no necesita venv-separator). "
             "Si falta el venv-separator, cae automáticamente a demucs",
    )
    parser.add_argument(
        "--conservar-stems", action="store_true",
        help="No borrar los archivos .wav intermedios de cada instrumento separado"
    )
    # Overrides de instrumento por stem: número (1-70) o nombre del banco.
    parser.add_argument("--voz",      metavar="INSTR", help="Instrumento para el stem de voz (nº 1-70 o nombre). Por defecto: Choir Aahs")
    parser.add_argument("--bajo",     metavar="INSTR", help="Instrumento para el bajo. Por defecto: Electric Bass")
    parser.add_argument("--guitarra", metavar="INSTR", help="Instrumento para la guitarra. Por defecto: Nylon Guitar")
    parser.add_argument("--piano",    metavar="INSTR", help="Instrumento para el piano. Por defecto: Acoustic Grand Piano")
    parser.add_argument("--otros",    metavar="INSTR", help="Instrumento para el stem 'other'. Por defecto: Lead Synth")
    parser.add_argument(
        "--umbral-silencio", type=float, default=-35.0, metavar="DB",
        help="Descarta los stems cuyo nivel quede más de estos dB por debajo del "
             "stem más fuerte. Evita 'pistas fantasma' hechas de puro sangrado "
             "(típico del stem de voz en canciones instrumentales) y libera "
             "pistas y polifonía. Usa un valor muy negativo (p.ej. -200) para "
             "desactivar el filtro. Por defecto: -35",
    )
    parser.add_argument(
        "--auto-instrumentos", action="store_true",
        help="Elige el instrumento de cada stem escuchando el audio separado y "
             "comparándolo con los samples reales del .sf2 del juego (dentro de la "
             "misma familia). La voz aguda recibe el 2º mejor candidato, para que "
             "las dos sub-pistas no dupliquen el mismo timbre. Los flags manuales "
             "(--voz, --guitarra, ...) tienen prioridad sobre la elección automática",
    )
    parser.add_argument(
        "--listar-instrumentos", action="store_true",
        help="Imprime la paleta completa del banco de Smash Remix y termina"
    )
    args = parser.parse_args()

    if args.listar_instrumentos:
        print_instrument_bank()
        return

    if args.entrada is None:
        sys.exit("ERROR: falta el archivo de audio de entrada. Usa -h para ayuda.")
    if not args.entrada.exists():
        sys.exit(f"ERROR: no existe el archivo de entrada: {args.entrada}")

    check_ffmpeg()

    salida = args.salida or args.entrada.with_suffix(".mid")
    profile = DENSITY_PROFILES[args.densidad]

    # RoFormer necesita el venv aislado; si no está, seguimos con Demucs en vez
    # de abortar (así el proyecto funciona recién clonado, sin el segundo venv).
    separador = args.separador
    if separador == "roformer" and not SEPARATOR_CLI.exists():
        log("AVISO: no se encontró venv-separator; se usa Demucs en lugar de RoFormer.")
        log("       Para habilitar RoFormer: pip install -r requirements-separator.txt")
        separador = "demucs"

    log(f"Densidad='{args.densidad}', separador='{separador}'")

    # Mapa efectivo de instrumentos: parte de los defaults y aplica los
    # overrides que el usuario haya pasado por CLI (--voz/--bajo/etc).
    stem_map = {k: v for k, v in SMASH_STEM_MAP.items()}
    overrides = {"vocals": args.voz, "bass": args.bajo, "guitar": args.guitarra,
                 "piano": args.piano, "other": args.otros}
    for stem, value in overrides.items():
        if value is not None:
            prog = resolve_instrument(value)
            stem_map[stem] = (prog, f"{stem.title()} ({SMASH_BANK[prog][0]})", False)
            log(f"  Instrumento '{stem}' -> {prog} = {SMASH_BANK[prog][0]}")

    with tempfile.TemporaryDirectory(prefix="stems_") as tmp:
        work_dir = Path(tmp)
        if separador == "roformer":
            stems = run_roformer_plus_demucs(args.entrada, work_dir, args.modelo, args.dispositivo)
        else:
            stems = run_demucs(args.entrada, work_dir, args.modelo, args.dispositivo)

        # --- Gate de energía: descartar stems sin contenido real ---
        # Demucs SIEMPRE devuelve sus 6 stems, tenga o no la canción cada uno.
        # En un instrumental, el stem de voz queda con puro sangrado y genera
        # "pistas fantasma" que gastan slots y polifonía de la N64 sin aportar
        # música. Se mide el nivel de cada stem y se descartan los que quedan
        # muy por debajo del más fuerte.
        stems = apply_energy_gate(stems, args.umbral_silencio)

        # --- Paso de "ingeniero de sonido": elegir instrumento por timbre ---
        # Se escucha cada stem y se compara con los samples reales del .sf2,
        # dentro de su misma familia. El 2º mejor va a la voz aguda para que
        # las dos sub-pistas no dupliquen el mismo timbre.
        # Alternos por registro: por defecto los curados; la selección
        # automática puede reemplazarlos si encuentra algo mejor.
        register_alt: dict[str, tuple[int, str]] = {
            stem: (prog, f"{stem.title()} ({SMASH_BANK[prog][0]})")
            for stem, prog in REGISTER_ALT_DEFAULT.items()
        }
        if args.auto_instrumentos:
            import timbre

            sf2_path = timbre.find_sf2()
            if sf2_path is None:
                log("AVISO: no se encontró el .sf2 del juego; se omite la selección automática.")
            else:
                log(f"Eligiendo instrumentos por timbre (sf2: {sf2_path.name})...")
                for name, wav_path in stems.items():
                    if overrides.get(name):
                        continue  # el flag manual manda
                    try:
                        ranking = timbre.rank_candidates(name, wav_path, sf2_path)
                    except Exception as exc:  # noqa: BLE001
                        log(f"  AVISO: no se pudo analizar '{name}' ({exc}); se deja el default.")
                        continue
                    if not ranking:
                        continue
                    best, dist = ranking[0]
                    # Si ni el mejor candidato se parece lo suficiente, el stem no
                    # tiene equivalente en el banco (típico de la voz: los coros
                    # 44/45/67 no tienen sample). Se respeta el default curado en
                    # vez de forzar una elección mala.
                    if dist > timbre.AUTO_MAX_DISTANCE:
                        actual = stem_map.get(name, (0, "?", False))[0]
                        log(f"  {name}: sin coincidencia clara (dist {dist:.2f}); "
                            f"se mantiene {SMASH_BANK.get(actual, ('?',))[0]} ({actual})")
                        continue
                    stem_map[name] = (best, f"{name.title()} ({SMASH_BANK[best][0]})", False)
                    log(f"  {name}: {SMASH_BANK[best][0]} ({best})  [dist {dist:.2f}]")
                    if len(ranking) > 1:
                        alt = ranking[1][0]
                        register_alt[name] = (alt, f"{name.title()} ({SMASH_BANK[alt][0]})")
                        log(f"      voz aguda -> {SMASH_BANK[alt][0]} ({alt})")

        stem_midis = {}
        for name, wav_path in stems.items():
            # La batería siempre se transcribe con máxima fidelidad (ver
            # DRUMS_TRANSCRIBE_PROFILE); el resto usa la densidad elegida.
            stem_profile = DENSITY_PROFILES[DRUMS_TRANSCRIBE_PROFILE] if name == "drums" else profile
            try:
                stem_midis[name] = transcribe_stem(wav_path, stem_profile)
            except Exception as exc:  # noqa: BLE001
                log(f"  AVISO: no se pudo transcribir '{name}' ({exc}); se omite ese stem.")

        if args.conservar_stems:
            destino_stems = salida.with_name(salida.stem + "_stems")
            destino_stems.mkdir(exist_ok=True)
            for name, wav_path in stems.items():
                shutil.copy(wav_path, destino_stems / wav_path.name)
            log(f"Stems .wav conservados en: {destino_stems}")

        assemble_midi(stem_midis, salida, args.voces_por_stem, profile, stem_map, register_alt)


if __name__ == "__main__":
    main()