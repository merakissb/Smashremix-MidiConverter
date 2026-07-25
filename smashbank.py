#!/usr/bin/env python3
"""
smashbank.py
============

Datos y lógica de mapeo de instrumentos del banco de Smash Remix (build
"Impecable"). Módulo puro: solo tablas y funciones de lookup, sin I/O de MIDI
ni de audio. `midi_a_smash.py` lo usa para decidir a qué instrumento de Smash
se remapea cada pista de un MIDI existente.

Reglas duras del motor de Smash (no negociables)
------------------------------------------------
- El "program byte" de cada pista DEBE estar en 1-70. Un valor fuera de ese
  rango (0, o 71+) apaga TODO el audio del juego cuando ese canal entra.
- La batería es SIEMPRE el programa 18 (Main Percussion). El canal MIDI es
  indiferente: en Smash solo manda el número de programa.
- Máximo 16 pistas.

Sobre la segunda columna del banco ("nombre GM")
------------------------------------------------
En `SMASH_BANK`, el segundo valor es el nombre General MIDI del MISMO número de
slot; el primero es el instrumento REAL que Smash puso ahí. Por eso importar un
MIDI GM tal cual suena mal: un "Distortion Guitar" GM vive en el slot 30, que en
Smash es un Trombón. El remapeo correcto va del INSTRUMENTO GM de la pista al
slot cuyo instrumento real es ese sonido (ver `GM_TO_SMASH`).
"""

from __future__ import annotations

# --- Reglas duras del motor ---
MAX_TRACKS = 16
SMASH_MIN_PROGRAM = 1
SMASH_MAX_PROGRAM = 70
SMASH_DRUM_PROGRAM = 18  # Main Percussion (obligatorio para la batería)

# ----------------------------------------------------------------------
# Banco completo de Smash Remix: slot de programa (1-70) ->
# (nombre real del instrumento, nombre GM que muestra el editor en ese slot).
# ----------------------------------------------------------------------
SMASH_BANK: dict[int, tuple[str, str]] = {
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

# ----------------------------------------------------------------------
# Nombres canónicos General MIDI (programa 0-127). Solo para el informe y
# para explicar de qué instrumento GM viene cada remapeo.
# ----------------------------------------------------------------------
GM_NAMES: list[str] = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord",
    "Clavi", "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba",
    "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ",
    "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ",
    "Accordion", "Harmonica", "Tango Accordion", "Acoustic Guitar (nylon)",
    "Acoustic Guitar (steel)", "Electric Guitar (jazz)",
    "Electric Guitar (clean)", "Electric Guitar (muted)", "Overdriven Guitar",
    "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass",
    "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin",
    "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings",
    "Orchestral Harp", "Timpani", "String Ensemble 1", "String Ensemble 2",
    "SynthStrings 1", "SynthStrings 2", "Choir Aahs", "Voice Oohs",
    "Synth Voice", "Orchestra Hit", "Trumpet", "Trombone", "Tuba",
    "Muted Trumpet", "French Horn", "Brass Section", "SynthBrass 1",
    "SynthBrass 2", "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax",
    "Oboe", "English Horn", "Bassoon", "Clarinet", "Piccolo", "Flute",
    "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle",
    "Ocarina", "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)",
    "Lead 8 (bass + lead)", "Pad 1 (new age)", "Pad 2 (warm)",
    "Pad 3 (polysynth)", "Pad 4 (choir)", "Pad 5 (bowed)", "Pad 6 (metallic)",
    "Pad 7 (halo)", "Pad 8 (sweep)", "FX 1 (rain)", "FX 2 (soundtrack)",
    "FX 3 (crystal)", "FX 4 (atmosphere)", "FX 5 (brightness)",
    "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)", "Sitar", "Banjo",
    "Shamisen", "Koto", "Kalimba", "Bag pipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum",
    "Melodic Tom", "Synth Drum", "Reverse Cymbal", "Guitar Fret Noise",
    "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter",
    "Applause", "Gunshot",
]


def gm_name(program: int) -> str:
    """Nombre GM legible de un programa 0-127."""
    return GM_NAMES[program] if 0 <= program < len(GM_NAMES) else f"programa {program}"


# ----------------------------------------------------------------------
# Mapa de respaldo GM (programa 0-127) -> slot de Smash (1-70), por familia
# tímbrica. Se elige el slot cuyo instrumento real más se parece al GM. Cubre
# los 128 programas y TODOS caen en 1-70 (nunca corta el audio del juego).
# ----------------------------------------------------------------------
def _build_gm_to_smash() -> dict[int, int]:
    m: dict[int, int] = {}
    # Piano (0-7) -> Acoustic Grand (52)
    for p in range(0, 8):
        m[p] = 52
    # Percusión cromática (8-15) -> Glockenspiel (10); marimba/xilófono -> 66
    for p in range(8, 16):
        m[p] = 10
    m[12] = m[13] = 66
    # Órgano (16-23) -> Organ (2); rock -> 43, church -> 47
    for p in range(16, 24):
        m[p] = 2
    m[18] = 43
    m[19] = 47
    # Guitarra (24-31)
    m[24] = m[25] = 38          # nylon/steel -> Nylon Guitar
    m[26] = m[27] = m[28] = 39  # jazz/clean/muted -> Muted Guitar
    m[29] = m[31] = 41          # overdrive/harmonics -> Overdriven Guitar
    m[30] = 42                  # distortion -> Distortion Guitar
    # Bajo (32-39)
    m[32] = m[35] = 32          # acoustic/fretless -> Acoustic Bass
    m[33] = m[34] = 13          # finger/pick -> Electric Bass
    m[36] = m[37] = 11          # slap -> Slap Bass
    m[38] = m[39] = 12          # synth bass -> Synth Bass
    # Cuerdas (40-47)
    for p in range(40, 45):
        m[p] = 7                # violín..tremolo -> Strings
    m[45] = 45                  # pizzicato -> Choir Oohs (slot pizz. de Smash)
    m[46] = 70                  # harp -> Harp
    m[47] = 17                  # timpani -> Timpani
    # Ensamble (48-55)
    for p in range(48, 52):
        m[p] = 7                # string ensembles/synthstrings -> Strings
    m[52] = m[54] = 15          # choir aahs/synth voice -> Choir Aahs
    m[53] = 45                  # voice oohs -> Choir Oohs
    m[55] = 22                  # orchestra hit -> Orchestral Hit
    # Metales (56-63)
    m[56] = 34                  # trumpet
    m[57] = 30                  # trombone
    m[58] = 3                   # tuba -> Synth Tuba
    m[59] = 40                  # muted trumpet
    for p in (60, 61, 62, 63):
        m[p] = 5                # french horn/brass/synthbrass -> Brass
    # Cañas (64-71)
    for p in (64, 65, 66, 67):
        m[p] = 50               # saxos -> Tenor Sax
    m[68] = m[69] = m[71] = 37  # oboe/english horn/clarinet -> Clarinet
    m[70] = 36                  # bassoon -> Bassoon
    # Tubos (72-79)
    for p in range(72, 80):
        m[p] = 16               # flautas -> Pan Flute
    m[72] = m[73] = m[78] = m[79] = 1  # piccolo/flute/whistle/ocarina -> Flute
    # Synth Lead (80-87)
    m[80] = 19                  # square -> Square Wave (NES)
    m[81] = 58                  # sawtooth -> Sawtooth (K64)
    for p in range(82, 88):
        m[p] = 6                # resto -> Lead Synth
    # Synth Pad (88-95) -> Synth Wave (4)
    for p in range(88, 96):
        m[p] = 4
    # Synth FX (96-103) -> Lead Synth (6)
    for p in range(96, 104):
        m[p] = 6
    # Étnicos (104-111)
    m[104] = m[106] = m[107] = 62  # sitar/shamisen/koto -> Shamisen
    m[105] = 14                     # banjo -> Banjo
    m[108] = 9                      # kalimba -> Kalimba
    m[109] = 16                     # bagpipe -> Pan Flute
    m[110] = 7                      # fiddle -> Strings
    m[111] = 37                     # shanai -> Clarinet
    # Percusivos (112-119)
    m[112] = 10                     # tinkle bell -> Glockenspiel
    m[113] = m[114] = 33            # agogo/steel drums -> Steel Drums
    m[115] = m[116] = m[117] = m[119] = SMASH_DRUM_PROGRAM  # woodblock/taiko/tom/cymbal
    m[118] = 25                     # synth drum -> TR-808 Synth Drum
    # SFX (120-127) -> Lead Synth (6), respaldo seguro dentro de rango
    for p in range(120, 128):
        m[p] = 6
    return m


GM_TO_SMASH: dict[int, int] = _build_gm_to_smash()

# ----------------------------------------------------------------------
# Overrides por PALABRA CLAVE en el nombre de la pista. Se revisan ANTES que
# el número de programa GM porque en rips de VGM el nombre suele ser más fiable
# (ejemplo real: "Bass (Sawtooth)" con programa GM 81 = Lead sawtooth). El
# orden importa: la primera clave que aparece en el nombre gana, así que las
# claves más específicas van primero.
# ----------------------------------------------------------------------
NAME_KEYWORDS: list[tuple[tuple[str, ...], int]] = [
    (("square",), 19),                                   # Square Wave (NES)
    (("triangle", "triángulo"), 20),                     # Triangle (NES)
    (("noise", "ruido"), 21),                            # White Noise (NES)
    (("choir", "vocal", "voice", "coro", "aah", "ooh"), 15),
    (("trumpet", "trompeta"), 34),
    (("trombone", "trombón", "trombon"), 30),
    (("brass", "metal"), 5),
    (("organ", "órgano", "organo"), 2),
    (("piano", "grand"), 52),
    (("guitar", "guitarra"), 38),
    (("string", "cuerda"), 7),
    (("flute", "flauta"), 1),
    (("harp", "arpa"), 70),
    (("banjo",), 14),
    (("lead", "synth"), 6),                              # lead/synth genérico
]


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


SMASH_NAME_TO_PROGRAM: dict[str, int] = {
    _norm(real): slot for slot, (real, _gm) in SMASH_BANK.items()
}


def resolve_instrument(value: str) -> int:
    """Convierte lo que pasa el usuario (número 1-70 o nombre del banco) en el
    slot de programa. Lanza ValueError con mensaje claro si no es válido."""
    value = value.strip()
    if value.isdigit():
        slot = int(value)
        if not (SMASH_MIN_PROGRAM <= slot <= SMASH_MAX_PROGRAM):
            raise ValueError(f"programa {slot} fuera de rango 1-70")
        return slot
    slot = SMASH_NAME_TO_PROGRAM.get(_norm(value))
    if slot is None:
        raise ValueError(f"instrumento '{value}' no existe en el banco")
    return slot


def slot_from_name(name: str) -> int | None:
    """Slot de Smash sugerido por el nombre de la pista, o None si no hay pista.

    Un 'bass' sawtooth/synth suena mejor como Synth Bass; un 'bass' cualquiera,
    como Electric Bass. Se resuelve antes de la lista genérica para que 'saw'/
    'square' no lo arrastren hacia un lead.
    """
    low = name.lower()
    if "bass" in low or "bajo" in low:
        return 12 if ("saw" in low or "synth" in low) else 13
    for keys, slot in NAME_KEYWORDS:
        if any(k in low for k in keys):
            return slot
    return None


def choose_slot(is_drum: bool, program: int, track_name: str) -> tuple[int, str]:
    """Decide el slot de Smash (1-70) para una pista y explica el porqué.

    Prioridad: (a) canal 10 -> batería 18; (b) nombre de la pista; (c) tabla GM.
    """
    if is_drum:
        return SMASH_DRUM_PROGRAM, "canal 10 (percusión)"
    by_name = slot_from_name(track_name)
    if by_name is not None:
        return by_name, f"nombre «{track_name.strip()}»"
    slot = GM_TO_SMASH.get(program, 52)
    return slot, f"GM {program} ({gm_name(program)})"


def instrument_name(slot: int) -> str:
    """Nombre real del instrumento de Smash en ese slot."""
    return SMASH_BANK.get(slot, ("?", "?"))[0]


def format_bank() -> str:
    """Devuelve el banco completo formateado para imprimir."""
    lines = [
        "Banco de Smash Remix (slot -> instrumento real  [nombre GM del editor])",
        "=" * 74,
    ]
    for slot in sorted(SMASH_BANK):
        real, gm = SMASH_BANK[slot]
        mark = "  <- percusión (batería)" if slot == SMASH_DRUM_PROGRAM else ""
        lines.append(f"  {slot:>2}  {real:<26} [{gm}]{mark}")
    return "\n".join(lines)
