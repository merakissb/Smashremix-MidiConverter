#!/usr/bin/env python3
"""
midi_a_smash.py
===============

Adapta un MIDI YA EXISTENTE (rip de NES/SNES/otras consolas, o cualquier .mid
General MIDI) al banco de Smash Remix, para importarlo con el Goldeneye Setup
Editor. NO transcribe ni inventa notas: copia el MIDI a nivel de mensaje —
notas, velocities, tempo, timing: todo idéntico— y solo reescribe el instrumento
de cada pista al slot de Smash (1-70) que mejor suena.

Filosofía
---------
El objetivo es que el resultado sea lo más parecido posible al MIDI original,
pero reproducible perfectamente por el motor de audio del juego. Eso implica dos
cosas que esta herramienta cuida:
  1. Fidelidad musical: no se toca ni una nota ni un tick de tempo.
  2. Compatibilidad con el motor: todo programa queda en 1-70 (fuera de rango
     apaga el audio), la batería va al programa 18, se respeta el tope de 16
     pistas y se limpian mensajes que el soundfont de Smash no entiende.

Decisión de cada instrumento (en orden de prioridad)
----------------------------------------------------
  a) Override manual del usuario (--pista "Nombre=Instrumento").
  b) Canal 10 (percusión GM)                 -> programa 18 (regla dura).
  c) Palabra clave en el nombre de la pista  -> más fiable que el programa GM
     en rips de VGM ("Bass (Sawtooth)", "Lead (Square)", "Guitar1"...).
  d) Tabla GM (0-127) -> Smash por familia, remapeando CADA program change de
     la pista de forma independiente.

Ver el banco y la lógica de mapeo en `smashbank.py`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import mido

import smashbank as sb

# Controladores de "bank select" (MSB=0, LSB=32). Se eliminan: seleccionan un
# banco de soundfont que en Smash no existe y podrían enmudecer o desviar la
# pista. El motor de Smash elige el instrumento solo por el número de programa.
BANK_SELECT_CONTROLLERS = {0, 32}

# Umbral informativo de polifonía. No es un límite exacto del hardware (el
# driver de audio de la N64 varía), pero por encima de esto conviene revisar en
# el juego si se cortan voces.
POLYPHONY_SOFT_LIMIT = 24


def _iter_channel_messages(track):
    for msg in track:
        if hasattr(msg, "channel"):
            yield msg


def track_channel(track) -> int | None:
    """Primer canal MIDI que aparece en la pista (None si es solo meta)."""
    return next((m.channel for m in _iter_channel_messages(track)), None)


def track_programs(track) -> list[int]:
    """Programas GM usados por la pista, en orden de aparición."""
    return [m.program for m in track if m.type == "program_change"]


def track_name(track) -> str:
    return next((m.name for m in track if m.type == "track_name"), "")


def track_note_count(track) -> int:
    return sum(1 for m in track if m.type == "note_on" and m.velocity > 0)


def peak_polyphony(midi: mido.MidiFile) -> int:
    """Máximo de notas sonando a la vez en toda la canción (todas las pistas)."""
    events: list[tuple[int, int]] = []
    for track in midi.tracks:
        t = 0
        for msg in track:
            t += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                events.append((t, 1))
            elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
                events.append((t, -1))
    # Ante empates de tiempo, procesar primero los note_off (-1) evita contar de
    # más una nota que termina justo cuando empieza otra.
    events.sort(key=lambda e: (e[0], e[1]))
    peak = cur = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def resolve_track_slot(
    track, overrides: dict[str, int]
) -> tuple[int | None, str]:
    """Slot forzado para toda la pista y su motivo, o (None, motivo) si cada
    program change debe mapearse por separado con la tabla GM."""
    name = track_name(track)
    if name in overrides:
        return overrides[name], "override CLI"
    if track_channel(track) == 9:  # canal 10 (0-indexed) = percusión
        return sb.SMASH_DRUM_PROGRAM, "canal 10 (percusión)"
    by_name = sb.slot_from_name(name)
    if by_name is not None:
        return by_name, f"nombre «{name.strip()}»"
    return None, "tabla GM por programa"


def remap_track(track, forced_slot: int | None) -> mido.MidiTrack:
    """Copia la pista reescribiendo instrumentos y limpiando bank-select.

    Si `forced_slot` es None, cada program change se mapea por su propio
    programa GM; si tiene valor, toda la pista usa ese slot.
    """
    channel = track_channel(track)
    out = mido.MidiTrack()

    # Si la pista tiene notas pero ningún program change, insertamos uno al
    # inicio para fijar el instrumento (si no, el juego usaría el slot 0 = corte).
    if track_note_count(track) and not track_programs(track):
        slot = forced_slot if forced_slot is not None else sb.GM_TO_SMASH.get(0, 52)
        out.append(mido.Message("program_change", channel=channel or 0,
                                program=slot, time=0))

    # Delta de los mensajes descartados: se arrastra al SIGUIENTE mensaje que se
    # emita (el delta representa el tiempo ANTES del mensaje), no al anterior.
    carry = 0
    for msg in track:
        if msg.type == "control_change" and msg.control in BANK_SELECT_CONTROLLERS:
            carry += msg.time
            continue
        new = msg.copy()
        new.time += carry
        carry = 0
        if new.type == "program_change":
            new.program = forced_slot if forced_slot is not None else \
                sb.GM_TO_SMASH.get(new.program, 52)
        out.append(new)
    # Bank-select como último mensaje (raro): su tiempo se conserva en el final.
    if carry and out:
        out[-1].time += carry
    return out


def merge_same_instrument(entries: list[dict]) -> list[dict]:
    """Fusiona pistas que van al mismo slot para caber en 16, sin perder notas.

    Solo se usa cuando hay más de 16 pistas con sonido. Las pistas de un mismo
    instrumento se combinan con `mido.merge_tracks`, que reordena por tiempo
    absoluto y conserva todas las notas.
    """
    by_slot: dict[int, list[dict]] = {}
    order: list[int] = []
    for e in entries:
        by_slot.setdefault(e["slot"], []).append(e)
        if e["slot"] not in order:
            order.append(e["slot"])

    merged: list[dict] = []
    for slot in order:
        group = by_slot[slot]
        if len(group) == 1:
            merged.append(group[0])
            continue
        combined = mido.merge_tracks([g["out"] for g in group])
        merged.append({
            "slot": slot,
            "out": combined,
            "name": f"{sb.instrument_name(slot)} (x{len(group)} fusionadas)",
            "notes": sum(g["notes"] for g in group),
            "reason": group[0]["reason"],
        })
    return merged


def convert(input_path: Path, output_path: Path, overrides: dict[str, int],
            drop_echo: bool) -> None:
    midi = mido.MidiFile(str(input_path))
    print(f"[midi_a_smash] Entrada: {input_path.name}  "
          f"(tipo {midi.type}, {len(midi.tracks)} pistas, {midi.length:.1f}s)")
    print("=" * 74)

    meta_tracks: list[mido.MidiTrack] = []
    sound_entries: list[dict] = []

    for track in midi.tracks:
        if track_note_count(track) == 0:
            # Tempo, marcadores, etc. se conservan; solo se limpian bank-selects
            # y se remapea cualquier program change suelto (por seguridad 1-70).
            meta_tracks.append(remap_track(track, None))
            continue
        name = track_name(track)
        if drop_echo and "echo" in name.lower():
            print(f"  [omitida por --sin-echo]  {name}")
            continue
        forced_slot, reason = resolve_track_slot(track, overrides)
        out = remap_track(track, forced_slot)
        # Slot representativo para el informe (el primero que quede en la pista).
        rep_slot = forced_slot if forced_slot is not None else next(
            (m.program for m in out if m.type == "program_change"), 52)
        sound_entries.append({
            "slot": rep_slot, "out": out, "name": name,
            "notes": track_note_count(track), "reason": reason,
        })

    if len(sound_entries) > sb.MAX_TRACKS:
        print(f"  AVISO: {len(sound_entries)} pistas con sonido; el juego admite "
              f"{sb.MAX_TRACKS}. Se fusionan las del mismo instrumento.")
        sound_entries = merge_same_instrument(sound_entries)
    if len(sound_entries) > sb.MAX_TRACKS:
        print(f"  AVISO: aún quedan {len(sound_entries)}; se recortan a "
              f"{sb.MAX_TRACKS} (se descartan las últimas).")
        sound_entries = sound_entries[:sb.MAX_TRACKS]

    for e in sound_entries:
        label = e["name"] or "(sin nombre)"
        print(f"  [{label}]  {e['notes']} notas  ->  "
              f"{e['slot']} = {sb.instrument_name(e['slot'])}   ({e['reason']})")

    out_midi = mido.MidiFile(type=midi.type, ticks_per_beat=midi.ticks_per_beat)
    out_midi.tracks.extend(meta_tracks)
    out_midi.tracks.extend(e["out"] for e in sound_entries)

    _validate_programs(out_midi)

    peak = peak_polyphony(out_midi)
    print("=" * 74)
    print(f"[midi_a_smash] Polifonía máxima: {peak} notas simultáneas", end="")
    if peak > POLYPHONY_SOFT_LIMIT:
        print(f"  (alta: revisa en el juego si se cortan voces)")
    else:
        print()

    out_midi.save(str(output_path))
    print(f"[midi_a_smash] Listo -> {output_path}")


def _validate_programs(midi: mido.MidiFile) -> None:
    """Aborta si algún program byte quedó fuera de 1-70 (apagaría el audio)."""
    for track in midi.tracks:
        for msg in track:
            if msg.type == "program_change" and not (
                    sb.SMASH_MIN_PROGRAM <= msg.program <= sb.SMASH_MAX_PROGRAM):
                sys.exit(f"ERROR: quedó un programa {msg.program} fuera de 1-70; "
                         "se aborta (apagaría todo el audio del juego).")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Adapta un MIDI existente al banco de Smash Remix "
                    "(copia las notas exactas, solo remapea instrumentos).")
    parser.add_argument("entrada", nargs="?", help="archivo .mid de entrada")
    parser.add_argument("-o", "--salida", help="archivo .mid de salida "
                        "(por defecto: <entrada>_smash.mid)")
    parser.add_argument("--sin-echo", action="store_true",
                        help="omite las pistas cuyo nombre contiene 'echo'")
    parser.add_argument("--pista", action="append", default=[], metavar="NOMBRE=INSTR",
                        help="fuerza el instrumento de una pista por su nombre exacto, "
                             "p.ej. --pista 'Guitar1=Distortion Guitar' (repetible)")
    parser.add_argument("--listar-instrumentos", action="store_true",
                        help="muestra el banco de Smash Remix y termina")
    args = parser.parse_args()

    if args.listar_instrumentos:
        print(sb.format_bank())
        return
    if not args.entrada:
        parser.error("falta el archivo .mid de entrada (o usa --listar-instrumentos)")

    input_path = Path(args.entrada)
    if not input_path.exists():
        sys.exit(f"ERROR: no existe el archivo '{input_path}'.")
    output_path = Path(args.salida) if args.salida else \
        input_path.with_name(input_path.stem + "_smash.mid")

    overrides: dict[str, int] = {}
    for item in args.pista:
        if "=" not in item:
            sys.exit(f"ERROR: --pista mal formado: '{item}'. Usa NOMBRE=INSTRUMENTO.")
        key, value = item.split("=", 1)
        try:
            overrides[key.strip()] = sb.resolve_instrument(value)
        except ValueError as exc:
            sys.exit(f"ERROR: {exc}. Usa --listar-instrumentos para ver los nombres.")

    convert(input_path, output_path, overrides, args.sin_echo)


if __name__ == "__main__":
    main()
