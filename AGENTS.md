# AGENTS.md

Reglas para agentes de código que trabajen en este repositorio.

## Antes de tocar nada

- Lee `docs/ARCHITECTURE.md` (sobre todo «Python indexing», «Symbol
  resolution» y «Checker») antes de cambiar `indexing.py`, `symbols.py`,
  `checker.py` o `mcp_server.py`.
- El núcleo (`indexing.py`, `symbols.py`, `checker.py`, `search.py`,
  `docsets.py`, `markdown_index.py`, `deps.py`, `node_indexing.py`) no
  importa `fastapi` ni `api.py`: recibe una conexión sqlite y diccionarios.
- Cada hilo usa su propia conexión (`db.Database.conn()`); nunca compartas
  una conexión entre hilos ni la pases a una tarea en segundo plano (las
  tareas reciben la suya).

## Reglas duras

- **Cero falsos positivos.** El comprobador solo da `error` si el espacio de
  nombres es estáticamente completo (`symbols.SymbolIndex.certainty_of_absence`).
  Si añades un caso dinámico nuevo, márcalo en los metadatos de
  `indexing.py` y añade el caso a `tests/fixtures/edgelib` y a
  `tests/test_checker_edges.py`.
- Escribe primero el test que falla y después el arreglo.
- `mcp_server.py` es un script independiente: solo stdlib, `httpx` y `mcp`.
- Una herramienta nueva en `/api/agent/<tool>`: salida compacta con ids y
  `truncated`/`has_more`, registrada con `call_tool(...)`, con su tool MCP
  (docstring + línea `Keywords:` en inglés y español, anotaciones honestas)
  y documentada en `docs/MCP.md`. La interfaz web usa sus propios endpoints,
  nunca `/api/agent/*`.
- Nada de `window.confirm`/`alert` en el frontend; textos en `i18n.ts`
  (inglés y español de España).
- Commits en inglés, pequeños y sin nombres de otros productos ni datos
  personales.

## Antes de dar algo por terminado

- `pytest -q` en el `.venv` del repo (sin red, menos de 90 s).
- `npm run build` en `frontend/` sin errores de TypeScript.
- Si cambias la interfaz: `python -m babels_hoard --demo --no-browser
  --port 18811` y `python scripts/screenshots.py http://127.0.0.1:18811`,
  y mira las capturas.
- README.md y README.es.md solo pueden afirmar lo que has ejecutado.
