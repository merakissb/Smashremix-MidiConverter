# mp3tomidi — de MP3 a MIDI para Super Smash Bros. 64 (Smash Remix)

Convierte una canción `.mp3` en un archivo `.mid` multipista listo para importarse
al juego **Super Smash Bros. 64 / Smash Remix** (build "Impecable").

No es un conversor MP3→MIDI genérico: la salida está **mapeada al banco de
instrumentos propio de Smash Remix** (70 instrumentos, NO General MIDI) y respeta
sus reglas duras, para que la música suene correctamente dentro del juego.

El `.mid` que produce este programa es el paso previo a convertirlo al formato de
secuencia de la N64 con el **Goldeneye Setup Editor** (ver el readme del repo
`smashremix` para ese paso).

---

## Qué hace (pipeline)

```
   cancion.mp3
       │
       │  1) Separación de fuentes
       ▼
   ┌──────────────────────────────────────────────┐
   │  Demucs  (htdemucs_6s)                        │  →  6 stems .wav:
   │  ó  RoFormer (voz) + Demucs (resto)           │     vocals, drums, bass,
   └──────────────────────────────────────────────┘     guitar, piano, other
       │
       │  2) Transcripción audio → notas MIDI (por stem)
       ▼
   ┌──────────────────────────────────────────────┐
   │  basic-pitch  (Spotify)                       │  →  notas por stem
   │  + filtros de densidad (nota mínima,          │
   │    bajo monofónico, tope de polifonía)        │
   └──────────────────────────────────────────────┘
       │
       │  3) Ensamblado + mapeo al banco Smash Remix
       ▼
   ┌──────────────────────────────────────────────┐
   │  pretty_midi                                  │  →  cancion.mid
   │  cada stem → su programa 1-70; drums = 18     │     (máx. 16 pistas)
   └──────────────────────────────────────────────┘
```

1. **Separación de fuentes** — separa la canción en instrumentos (stems `.wav`).
   - `demucs` (por defecto): `htdemucs_6s` de Meta, 6 stems en un paso.
   - `roformer`: **Mel-Band/BS-RoFormer** aísla la voz (calidad superior) y luego
     Demucs corre sobre el instrumental sin voz para el resto de los stems.
2. **Transcripción** — `basic-pitch` (Spotify) convierte cada stem de audio a notas
   MIDI. Se aplican filtros para controlar la densidad de notas.
3. **Ensamblado** — `pretty_midi` junta todo en un `.mid`, asignando a cada stem el
   **programa del banco Smash Remix** correspondiente.

---

## El mapeo al banco de Smash Remix (lo importante)

El banco del juego **no es General MIDI**: son 70 instrumentos y el número de
programa es un índice directo a ese banco. Reglas duras (documentadas en el
repositorio de **Smash Remix**, sección "Tabla de instrumentos" de su readme):

- **Todo programa debe estar entre 1 y 70.** Un programa fuera de rango (0, o 71+)
  **corta TODO el audio del juego** en el momento en que entra ese canal. Es el
  fallo más común y difícil de diagnosticar. El programa **valida esto antes de
  escribir** y aborta si algo se sale de rango.
- **El programa 18 es la percusión** (Main Percussion). No hay canal de batería
  reservado como en GM: el canal es indiferente, solo manda el número de programa.
- **Máx. 16 pistas** (una por canal MIDI).

Mapeo stem → instrumento (definido en `SMASH_STEM_MAP`):

| Stem   | Prog | Instrumento Smash Remix |
|--------|------|-------------------------|
| vocals | 15   | Choir Aahs              |
| piano  | 52   | Acoustic Grand Piano    |
| guitar | 38   | Nylon Guitar            |
| bass   | 13   | Electric Bass           |
| drums  | 18   | Main Percussion (fijo)  |
| other  | 6    | Lead Synth              |

---

## Instalación

Requiere **Python 3.9–3.11** y **ffmpeg** instalado en el sistema.

```bash
# ffmpeg (Linux / Mac)
sudo apt-get install ffmpeg        # o: brew install ffmpeg

# Entorno principal (Demucs + basic-pitch)
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

La primera ejecución de Demucs descarga los pesos del modelo (~80–300 MB).

### Opcional: separación RoFormer (`--separador roformer`)

RoFormer (`audio-separator`) exige `numpy>=2`, **incompatible** con el `numpy<2`
que necesitan Demucs/basic-pitch. Por eso vive en un **venv aparte** y se invoca
como subproceso:

```bash
python3 -m venv venv-separator
venv-separator/bin/pip install -r requirements-separator.txt
```

El programa principal busca automáticamente `venv-separator/bin/audio-separator`.
Si no existe, usa `--separador demucs` (no necesita este segundo entorno).

---

## Uso

```bash
# Básico (Demucs, densidad media)
venv/bin/python mp3_a_midi_gm.py cancion.mp3

# Especificar salida y usar GPU
venv/bin/python mp3_a_midi_gm.py cancion.mp3 -o salida.mid --dispositivo cuda

# Máxima calidad y fidelidad: RoFormer + densidad media (por defecto)
venv/bin/python mp3_a_midi_gm.py cancion.mp3 --separador roformer --dispositivo cuda
```

### Opciones

| Opción              | Valores                          | Por defecto | Qué hace |
|---------------------|----------------------------------|-------------|----------|
| `entrada`           | archivo `.mp3`                   | —           | Canción de entrada (obligatorio). |
| `-o, --salida`      | ruta `.mid`                      | `<entrada>.mid` | Archivo MIDI de salida. |
| `--separador`       | `demucs`, `roformer`             | `demucs`    | Motor de separación (ver arriba). |
| `--densidad`        | `alta`, `media`, `baja`          | `media`     | `alta` = máxima fidelidad; `media` = fiel con poda suave; `baja` = recorte fuerte (solo para canciones muy densas, puede sonar pobre). |
| `--voces-por-stem`  | `1`, `2`, `3`                    | `2`         | Divide vocals/piano/guitar/other en sub-pistas por registro (agudo/grave). Batería y bajo siempre en 1 pista. |
| `--modelo`          | `htdemucs_6s`, `htdemucs`, …     | `htdemucs_6s` | Modelo de Demucs. Solo `htdemucs_6s` da los 6 stems por instrumento. |
| `--dispositivo`     | `cpu`, `cuda`                    | `cpu`       | Usar `cuda` si tienes GPU NVIDIA (mucho más rápido). |
| `--conservar-stems` | (flag)                           | off         | No borra los `.wav` intermedios de cada stem. |

### Densidad de notas

`basic-pitch` tiende a sobre-transcribir, y la N64 tiene **polifonía limitada**
(corta notas en pasajes densos). La filosofía aquí es **preservar la originalidad**:
en vez de subir los umbrales de `basic-pitch` (que tira notas reales a ciegas y
deja un sonido pobre, "8-bit"), se deja que capture (casi) toda la canción y se
reduce densidad con post-filtros quirúrgicos que conservan las notas más presentes:

- **`min_note_seconds`** — descarta notas muy cortas (ruido de transcripción).
- **bajo monofónico** — el stem de bajo se colapsa a una sola nota a la vez.
- **`max_voices`** — tope de notas simultáneas por stem, conservando las de mayor
  velocity (las más audibles); `0` = sin tope.

La batería siempre se transcribe con fidelidad máxima (no se le aplica la densidad
elegida), porque ya se detecta pobremente y los umbrales altos la borran.

**Empieza con `--densidad media`** (por defecto): fiel y con poda suave. Usa `alta`
para máxima fidelidad, y `baja` solo para canciones realmente saturadas.

---

## Después: importar al juego

El `.mid` que produce este programa se convierte al formato de la N64 con el
**Goldeneye Setup Editor** (`Tools > Extra Tools > MIDI Tools > Convert Midi to GE
Format and Loop`), y luego se registra en el repositorio de **Smash Remix**.
Ese proceso (loop, volumen CC7, registro en `midi.asm`/`Toggles.asm`/`SRAM.asm`)
está documentado en el readme de ese repositorio.

---

## Limitaciones conocidas

- **Batería**: `basic-pitch` transcribe instrumentos *afinados* (por tono), y la
  batería es percusión *no afinada*, así que se detecta pobremente. Además, el
  mapeo nota→sonido dentro del programa 18 (Main Percussion) de la N64 no está
  documentado y puede no coincidir con la convención GM que usa el código. Es el
  punto más débil del pipeline (mejora pendiente: detección por *onsets*).
- **Densidad**: aun con `--densidad baja`, canciones muy densas pueden exceder la
  polifonía de la N64. Ajustar caso a caso.
- **`other`**: el stem "other" de Demucs recoge todo lo no clasificado y tiende a
  quedar saturado.

---

## Estructura

| Archivo | Contenido |
|---|---|
| `mp3_a_midi_gm.py` | El programa completo (separación + transcripción + ensamblado). |
| `requirements.txt` | Dependencias del entorno principal (numpy<2). |
| `requirements-separator.txt` | Dependencias del venv aislado de RoFormer (numpy≥2). |
| `venv/` | Entorno principal (no versionado). |
| `venv-separator/` | Entorno del separador RoFormer (no versionado). |
