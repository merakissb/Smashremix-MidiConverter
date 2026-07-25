# midi-a-smash

Adapta un **MIDI ya existente** (rip de NES/SNES/Génesis/arcade, o cualquier
`.mid` General MIDI) al **banco de instrumentos de Smash Remix**, para
importarlo con el *Goldeneye Setup Editor* y que suene en el juego.

No transcribe ni inventa notas: **copia el MIDI a nivel de mensaje** —notas,
velocities, tempo y timing quedan idénticos— y solo reescribe el instrumento de
cada pista al slot de Smash (1-70) que mejor lo representa. El entregable es lo
más parecido posible al original, pero reproducible por el motor de audio de la
N64.

> **¿Por qué partir de un MIDI y no de un MP3?** Porque las notas ya están
> escritas, exactas y con su timing perfecto. No hay un paso de transcripción
> que invente o pierda notas: el único problema que queda es el fácil y
> determinista de **elegir el instrumento equivalente**.

---

## Uso

```bash
pip install -r requirements.txt

# Conversión básica  ->  cancion_smash.mid
python midi_a_smash.py cancion.mid

# Elegir el nombre de salida
python midi_a_smash.py cancion.mid -o salida.mid

# Ver el banco completo de Smash Remix (slots 1-70)
python midi_a_smash.py --listar-instrumentos
```

### Ajustar instrumentos a mano

Si un instrumento no quedó como querías, fúerzalo por el **nombre exacto de la
pista** (el que aparece en el informe). Se puede repetir:

```bash
python midi_a_smash.py cancion.mid \
  --pista "Guitar1=Distortion Guitar" \
  --pista "Guitar2=Overdriven Guitar"
```

El instrumento puede darse por nombre (`Distortion Guitar`) o por número
(`42`). Usa `--listar-instrumentos` para ver los nombres válidos.

### Descartar pistas de eco

Muchos arreglos duplican una melodía en pistas "Echo" para dar reverb. Si esas
dobles voces sobran o gastan demasiadas voces del N64:

```bash
python midi_a_smash.py cancion.mid --sin-echo
```

---

## Cómo elige el instrumento de cada pista

En orden de prioridad:

1. **Override manual** (`--pista "Nombre=Instrumento"`).
2. **Canal 10 (percusión GM) → programa 18 (Main Percussion).** Regla dura del
   motor: la batería es siempre el 18, el canal es indiferente.
3. **Palabra clave en el nombre de la pista.** En rips de VGM el nombre suele
   ser más fiable que el número de programa (p. ej. "Bass (Sawtooth)" trae
   programa GM 81 = *lead sawtooth*, pero es un bajo).
4. **Tabla GM (0-127) → Smash por familia tímbrica.** Cada *program change* de
   la pista se remapea de forma independiente.

El mapeo completo vive en [`smashbank.py`](smashbank.py).

---

## Reglas del motor de Smash que la herramienta garantiza

| Regla | Por qué | Cómo se cumple |
|-------|---------|----------------|
| Todo programa en **1-70** | Un programa fuera de rango **apaga todo el audio** del juego | Se valida antes de escribir; aborta si algo se sale |
| Batería = **programa 18** | Es el kit de percusión (Main Percussion) | El canal 10 se rutea a 18 |
| Máximo **16 pistas** | Límite del motor | Si hay más, se **fusionan** las del mismo instrumento antes de descartar |
| Sin *bank-select* raros | Seleccionan bancos que el soundfont de Smash no tiene | Se eliminan los CC 0/32, conservando el timing |

Además, el informe reporta la **polifonía máxima** (notas simultáneas) para
avisar si conviene revisar en el juego que no se corten voces.

---

## Arquitectura

Dos módulos, sin dependencias pesadas (solo [`mido`](https://mido.readthedocs.io)):

- **`smashbank.py`** — datos y lógica de mapeo (banco 1-70, nombres GM, tabla
  GM→Smash, resolución por nombre). Módulo puro, sin I/O.
- **`midi_a_smash.py`** — lectura/escritura de MIDI con `mido` y la CLI.

### Sobre el nombre "GM" del banco

En el banco de Smash, cada slot muestra en el editor un nombre General MIDI que
corresponde al **mismo número de slot**, no al instrumento real que suena. Por
eso importar un MIDI GM tal cual suena mal: un *Distortion Guitar* GM cae en el
slot 30, que en Smash es un **Trombón**. El remapeo correcto va del instrumento
GM de la pista al slot cuyo instrumento *real* es ese sonido.

---

## Limitaciones conocidas

- **Kit de batería (programa 18):** se conservan los números de nota de
  percusión GM originales, pero el mapeo nota→sonido dentro del kit de Smash no
  está documentado; puede requerir ajuste manual tras probar en el juego.
- **Calidad del rip:** si el `.mid` trae nombres de pista descriptivos y/o
  programas GM significativos, el remapeo es muy bueno. Un dump crudo (todo
  "programa 0", sin nombres) caerá en los valores por defecto de familia.
