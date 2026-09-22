# AGENTS.md

Reglas para agentes de código que trabajen en este repositorio.

## Antes de tocar nada

- Lee `docs/ARCHITECTURE.md` y `docs/MCP.md` antes de cambiar `indexing.py`,
  `checker.py` o `mcp_server.py` - explican decisiones no obvias (por qué
  los alias no resueltos se registran igualmente, por qué el checker usa
  `unchecked` en vez de adivinar, por qué `find_distribution` hace dos
  pasadas).
- El núcleo (`indexing.py`, `checker.py`, `search.py`, `docsets.py`,
  `markdown_index.py`, `deps.py`, `node_indexing.py`) no debe importar
  `fastapi` ni nada de `api.py`. Solo toma una conexión sqlite y diccionarios
  planos. Esto es lo que permite testear sin arrancar un servidor.

## Reglas duras

- **Nunca** inventes un resultado. Si algo no se puede resolver con
  confianza (alias dinámico, tipo no resuelto), cuenta en `unchecked` y
  no reportes nada. Cero falsos positivos vale más que cobertura.
- `mcp_server.py` es un script standalone: solo puede importar stdlib,
  `httpx` y `mcp`. Nunca importes desde el paquete `babels_hoard`.
- Cualquier tool nuevo bajo `/api/agent/<tool>` debe:
  1. devolver JSON compacto (resultados por defecto de 5-10 items, con
     `truncated`/`has_more` cuando se corte),
  2. quedar registrado en `agent_calls` (usa `call_tool(...)` en `api.py`),
  3. tener su propio tool MCP en `mcp_server.py` con docstring + línea
     `Keywords: ... (inglés y español)`,
  4. añadirse a `faustus-plugin.json` si cambia el contrato del plugin.
- No uses `window.confirm`/`alert` en el frontend - usa confirmación en
  dos pasos inline.
- Todo texto de commit en inglés, sin nombrar otras apps ni datos
  personales, con los trailers exactos que pide el contrato.

## Antes de dar algo por terminado

- `pytest -q` en el `.venv` del repo (offline, <90s).
- `npm run build` en `frontend/` con cero errores de TypeScript.
- Si tocaste `indexing.py`/`checker.py`, corre también
  `pytest tests/test_indexing.py tests/test_checker.py -q` y revisa que el
  caso `fakelib_v1`/`fakelib_v2` siga demostrando la migración de API.
- Si tocaste `mcp_server.py` o `api.py`, corre
  `pytest tests/test_mcp_protocol.py -q` (arranca la app real y habla el
  protocolo MCP de verdad).
