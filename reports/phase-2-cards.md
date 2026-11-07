# Phase 2 — Tarjetas (cards) y busqueda FTS

## 1. Tipos de evento, nombres de campo y origen de cada campo de la tarjeta

Formato verificado: JSONL v4, un JSON por linea, `type` + `data` (y `time` en ms epoch y `seq` en todo evento). 146 logs, 0 lineas no analizables.

| Campo de la tarjeta | Tipo de evento | Campos fuente |
|---|---|---|
| `id` | `session` | `id` |
| `source` | — (del pipeline) | ruta del archivo `session.v4.jsonl.zstd` |
| `start` / `end` | todos | `min(time)` / `max(time)` (ms epoch → ISO UTC) |
| `cwd` | `session` | `cwd` |
| `first_message` | `user/message` con `data.source.kind == "user"` | `data.content` (lista de bloques `{type:"text",text}`), truncado a 300 chars |
| `files_touched` | `tool/call` (`data.name` en read/write/edit) y `tool/result` | args `file_path`/`path`; `data.meta.path` / `data.meta.paths`; dedup, orden primera-ocurrencia, max 40 |
| `bash_commands` | `tool/call` con `data.name == "bash"` | `data.arguments` (string JSON) → `command`; max 30, truncados a 120 chars |
| `tool_call_count` | `tool/call` | conteo |
| `error_count` | `tool/result` | `data.message.isError == true` |
| `commit_hashes` | `tool/result` (no error) | regex `\[[^\s\]]+ ([0-9a-f]{7,40})\>` sobre `data.message.content` (formato git `[main fa740e5]`); dedup, max 20 |

Otras lineas vistas (no usadas en tarjetas): `agent/inbox/spliced`, `assistant/message`, `request/header`, `session/title`, `job/registered`, `session/title-llm-request`, `workspace/changes`, `command/run`/`command/done`.

## 2. Cifras

- Sesiones procesadas: **146** (de 147 directorios de sesion; la sesion actual `session-8b1b3fb3` excluida con `--exclude`).
- Tarjetas escritas: **146** → `data/cards.jsonl`.
- Tamano del indice: **454656 bytes** → `data/library.db` (SQLite FTS5, tabla `cards` + virtual `cards_fts`).
- Tiempo de consulta (salida de `search.py`): 0.3–0.4 ms por consulta.

## 3. Fallos de parseo

**Ninguno.** 146/146 logs, 0 lineas no analizables, 0 fallos de zstd (ver linea de `extract.py`: `logs: 146  cards: 146  failures: 0`).

## 4. Tres temas: consulta, resultados, verificacion contra logs

**Tema 1: `compatibility.json` → 0 resultados** (consulta `compatibility.json`, 13 bytes, 0.3 ms; la palabra sola `compatibility` tambien 0). Verificacion en el log fuente: el termino SI existe (20 lineas en `session-f4c1b1f8`), pero solo en resultados de herramientas y comandos bash fuera de las capturas de la tarjeta (su tarjeta trae las 30 primeras bash_commands — toco el tope — y ninguna contiene el termino). Por regla de la tarea, se reemplaza con un tema real de la lista de tarjetas: **`dsh-recall`** → **2 lineas** de resultado, 430 bytes, 0.4 ms (`session-f4c1b1f8`, `session-41334c39`). **Verdadero**: en ambos logs el primer mensaje de usuario es "Work in ~/biblioteca. Phase 1 is done (reports/phase-1-recall-diagnostic.md, commit fa740e5)…" / "…This is Phase 1 of the biblioteca project" (112 y 96 lineas coincidentes).

**Tema 2: `toolFilter standard-light` → 0 resultados en AND** (13 bytes, 0.4 ms; `toolFilter` solo: 0). Con la mitad del tema que si aparece: **`standard-light`** → **3 lineas**, 653 bytes, 0.4 ms (`session-b450021f`, `session-86987b85`, `session-ac96b069`, todas cwd `/home/jkzero/dsh-scratch`). **Verdadero**: los 3 logs tienen `agentPreset: "standard-light"` en el evento `session`, y en `86987b85` el usuario pide "reemplazar el bloque de reglas … del preset Standard Light" (64/26/31 lineas coincidentes).

**Tema 3: `imgproxy or webp`** — ejecutado como OR (AND `imgproxy webp` da 0): `imgproxy` → **1 linea**, 213 bytes, 0.4 ms (`session-c5f86181`, cwd `/home/jkzero/rust-lab`); `webp` → 0. **Verdadero**: el primer mensaje de usuario es "TASK: read-only reconnaissance for a Rust rewrite of imgproxy. Do NOT modify…" (195 lineas coincidentes).

## 5. Comandos exactos para re-ejecutar

```bash
cd ~/biblioteca
python3 src/extract.py --exclude=session-8b1b3fb3
python3 src/build_index.py
python3 src/search.py dsh-recall
python3 src/search.py standard-light
python3 src/search.py imgproxy
python3 src/verify_match.py ~/.local/share/deepseek-harness/sessions/--home-jkzero-biblioteca--/session-f4c1b1f8-d9c2-442c-9e15-aeed9a7b6198 dsh-recall
python3 src/verify_match.py ~/.local/share/deepseek-harness/sessions/--home-jkzero-dsh-scratch--/session-86987b85-73cf-410f-bdeb-e5ff7ff1877f standard-light
python3 src/verify_match.py ~/.local/share/deepseek-harness/sessions/--home-jkzero-rust-lab--/session-c5f86181-d002-4a54-b7a1-7d647da9c2e4 imgproxy
```

## 6. git status (data/ sin rastrear)

```
?? src/build_index.py
?? src/extract.py
?? src/inspect_events.py
?? src/inspect_summary.py
?? src/search.py
?? src/verify_match.py
```

Con `git status --porcelain --ignored` y `git check-ignore -v`:

```
!! data/
.gitignore:1:data/    data/
.gitignore:1:data/    data/cards.jsonl
.gitignore:1:data/    data/library.db
```

`data/` esta ignorada por `.gitignore` (linea 1): nunca se comite.

## Option B — Familiaridad + recollection (indice de dos niveles)

### Que cambio
- `src/extract.py`: nuevo campo `familiar_terms` (perdido: todas las rutas de archivo de tool calls/results + todos los comandos bash, cada uno <=300 chars, sin tope de cantidad); `bash_commands` ordenado por saliencia (errores, `git commit`/`git push`, escrituras, el resto; cronologico dentro de cada prioridad, <=30 comandos x 120 chars); `files_touched` con escritos/edutados primero y solo-leidos despues (<=40).
- `src/build_index.py`: dos tablas FTS5: `cards_fts` (first_message, files_touched, bash_commands — recollection) y `familiar_fts` (familiar_terms, una fila por sesion — familiaridad); reconstruccion atomica (tmp + `os.replace`), idempotente.
- `src/search.py`: consulta `cards_fts` primero y luego `familiar_fts` excluyendo las sesiones ya devueltas; maximo 5 lineas; cada linea empieza con `[card]` o `[familiar]`, id de sesion, fecha, campo(s) que coincidio y snippet <=80 chars (FTS5 `snippet()`); cada palabra de la consulta va entrecomillada como frase FTS5 (puntos y guiones funcionan); imprime bytes y milisegundos.

### Tests
- **a) compatibility.json** — 3 aciertos (el archivo estaba mas alla del tope de 30 comandos y ahora se encuentra):
  - `[card]` session-8b1b3fb3 (commands_text)
  - `[familiar]` session-41334c39
  - `[familiar]` session-f4c1b1f8 (la sesion donde nacio el archivo)
- **b) standard-light** — 5 aciertos, evaluados uno a uno:
  - session-b450021f — TRUE (la sesion buscaba el prompt del preset Standard light)
  - session-86987b85 — TRUE (reemplazo de un bloque de reglas del preset)
  - session-ac96b069 — FALSE (solo aparece la ruta `dsh-preset-standard-light/...` en files/commands; la sesion trataba el espacio en disco de los modelos)
  - session-8b1b3fb3 — FALSE (solo el literal "standard-light" dentro de un comando de busqueda en commands_text)
  - session-6d7eea23 — FALSE (solo la ruta del preset en familiar_terms; la sesion trataba un problema de monitores)
- **c) venexuela** (tema de sesion antigua; palabra tomada de los first_message de las 10 tarjetas mas viejas) — 1 acierto `[card]` session-38b3c05c (first_message "quien es el presidente de venexuela?"); verificado contra el log crudo con `verify_match.py`: 3 lineas coincidentes (<=5 lineas / 200 chars).
- **d) Idempotencia** — extract + build_index ejecutados dos veces: sha256 de `data/cards.jsonl`, `data/library.db` y de las lineas de resultado de las busquedas a/b/c identicos entre corridas (la linea final con milisegundos es tiempo en vivo y queda fuera del hash).

### Antes / despues
| Metrica | Antes | Despues |
|---|---|---|
| Tamano de `data/library.db` | 454656 bytes | 1273856 bytes |
| compatibility.json, mediana (n=21, in-process) | 0.3 ms | 0.9 ms |
| standard-light, mediana (n=21) | 0.4 ms | 1.5 ms |
| venexuela, mediana (n=21) | 0.4 ms | 0.7 ms |

### Comandos usados
```
python3 src/extract.py --exclude=session-6e168499   # 147 logs, 147 cards, 0 fallos
python3 src/build_index.py
python3 src/search.py compatibility.json
python3 src/search.py standard-light
python3 src/search.py venexuela
python3 src/verify_match.py <dir-de-sesion> venexuela
```

### Git
`data/` sigue ignorada (`.gitignore` linea 1; `git check-ignore` la confirma); `git status` solo muestra los cambios de `src/`, `docs/decisions.md`, `docs/sources.md` y este reporte.
