# Babel's Hoard
### ¿Qué dice de verdad la versión que hay instalada?
**Un índice local y exacto por versión de las APIs de tus proyectos Python y JS/TS, y un comprobador de APIs alucinadas construido encima - para que un modelo de código deje de adivinar firmas a partir de datos de entrenamiento desactualizados.**

[English](README.md) · [Ejecutar en local](#ejecutar-en-local-en-windows) · [Conectar una IA](docs/MCP.md) · [Portfolio](https://luissalet.github.io/Portfolio/#projects)

![Vista de búsqueda: la consulta "send a get request" muestra httpx.Client.get con su firma real instalada](docs/media/search.png)
*Aplicación real, datos de demostración (el propio intérprete de Babel, indexado en el momento de construir la app).*

## Por qué

Los datos de entrenamiento de un modelo de código local son una mezcla de
versiones de librerías: escribe `df.append(...)` para pandas 2.x, pasa un
argumento con nombre que ya se renombró, importa un símbolo que cambió de
módulo, o inventa un parámetro que nunca existió. Buscar en la web es
lento, ruidoso y no específico de la versión que tienes instalada de
verdad. La fuente de la verdad ya está en el disco - el propio `.venv` y
`node_modules` del proyecto - Babel lo lee, lo indexa de forma estática, y
responde con la firma real, o te dice claramente que lo que pides no existe
y cuál es el nombre real más parecido.

![Vista de comprobar código: una llamada client.get(..., bogus_kw=1) se marca como argumento con nombre inesperado](docs/media/check-code.png)
*Aplicación real, datos de demostración. El comprobador nunca ejecuta el fragmento - lo analiza y resuelve los nombres contra el entorno indexado.*

## Qué está implementado

| Área | Disponible ahora | Límite |
| --- | --- | --- |
| Indexado de Python | Estático (griffe/AST, nunca ejecuta código del proyecto) de cualquier intérprete registrado, sus distribuciones instaladas y la stdlib, de forma perezosa y cacheada por (entorno, paquete, versión) | Tope de 1500 entradas y profundidad 8 por paquete (se marca `partial`, no se trunca en silencio); las extensiones compiladas sin stubs `.pyi` se registran como `note: "compiled, no stubs"` en vez de inventarse; un alias condicional entre módulos que griffe no puede resolver estáticamente (`os.path` es el ejemplo canónico) se registra como marcador sin resolver, no se describe por completo |
| Indexado JS/TS | API del compilador de TypeScript sobre los `.d.ts` de un paquete (campo types/typings, `exports[...].types`, `index.d.ts`, o un paquete `@types/<nombre>`); funciones, clases, interfaces, un nivel de miembros, JSDoc, `@deprecated` | Sin Node.js en el PATH degrada con un error claro, el resto de la app sigue funcionando; solo se indexan los miembros exportados de primer nivel más un nivel de miembros |
| Comprobación de alucinaciones/mal uso (`api_check_code`) | Imports/atributos inexistentes, argumentos con nombre inesperados, demasiados posicionales, argumento requerido ausente, uso de APIs obsoletas - todo con sugerencias "¿quisiste decir"; diseño de cero falsos positivos, demostrado contra un paquete de prueba con dos versiones (una API eliminada entre versiones) y contra fragmentos reales buenos/malos de httpx | Comprobación completa solo en Python; TypeScript es v1 (solo existencia de imports con nombre); el código dinámico, las clases base no resueltas y los valores sin tipo se reportan como `unchecked`, nunca se asumen correctos en silencio |
| Búsqueda de texto completo | SQLite FTS5, ponderado bm25 (nombre/qualname/firma/resumen/doc), tokenización consciente de camelCase/snake_case/puntos, filtros por librería/ecosistema/tipo/entorno | Sin búsqueda semántica/por embeddings - solo léxica |
| Docsets sin conexión | Catálogo e instalación del espejo de DevDocs (HTML convertido a Markdown, dividido por ancla de sección, indexado) | Solo el catálogo/instalación tocan la red, nada más; la conversión es un paso propio basado solo en la stdlib, no es pixel-perfect |
| Carpetas markdown | Indexa cualquier carpeta de `.md`/`.mdx`/`.rst`/`.txt` dividida por encabezado | Sin resolución de enlaces entre archivos |
| Auditoría del asistente | Cada llamada a `/api/agent/*` queda registrada (herramienta, resumen de argumentos, duración, ok/error) y se ve en "Actividad del asistente" | Solo local, no se exporta |
| Interfaz | App de escritorio en React 19: Buscar, Librerías (registrar entornos, indexar bajo demanda, indexar dependencias del proyecto en segundo plano), Comprobar código, Docsets, Actividad del asistente, Ajustes; claro/oscuro, inglés/español | - |

## Conectar con Faustus

La app se declara mediante `faustus-plugin.json`. Arranca Babel's Hoard y
luego, en Faustus: **Conectores → Apps cercanas → Añadir**.

| Herramienta MCP | Solo lectura | Qué hace |
| --- | --- | --- |
| `docs_libraries` | sí | Qué hay indexado, con versiones, y los entornos registrados |
| `docs_search` | sí | Búsqueda ordenada por relevancia entre APIs, docsets y markdown |
| `api_lookup` | sí* | Firma/parámetros/doc exactos de un símbolo (indexa bajo demanda la primera vez) |
| `api_check_code` | sí* | Encuentra APIs alucinadas/eliminadas/mal usadas en un fragmento |
| `docs_read` | sí | Lee una sección de docset o un docstring completo, por partes |
| `docs_add_environment` | no | Registra un proyecto/intérprete/node_modules |
| `docs_catalog` | sí | Lista docsets instalables sin conexión (red) |
| `docs_install_docset` | no | Descarga e indexa un docset (red) |
| `docs_index_folder` | no | Indexa una carpeta de documentación markdown |

Formas exactas de argumentos/respuesta, límites y ejemplos:
[docs/MCP.md](docs/MCP.md).

También funciona con cualquier cliente MCP por stdio, no solo con Faustus:

```json
{
  "mcpServers": {
    "babels-hoard": {
      "command": "/ruta/absoluta/a/babels-hoard/.venv/bin/python",
      "args": ["/ruta/absoluta/a/babels-hoard/babels_hoard/mcp_server.py"],
      "env": { "BABEL_URL": "http://127.0.0.1:8811" }
    }
  }
}
```

## Ejecutar en local en Windows

Haz doble clic en **`Iniciar Babel's Hoard.cmd`** (crea el entorno virtual,
instala dependencias, compila el frontend la primera vez, y arranca el
servidor), o manualmente:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements-lock.txt
cd frontend; npm ci; npm run build; cd ..
.venv\Scripts\python -m babels_hoard
```

Abre <http://127.0.0.1:8811>. Usa `--demo` para ejecutar contra datos
sintéticos en `data-demo/` (registra el propio intérprete de Babel e indexa
algunos paquetes reales, para que la interfaz tenga firmas reales que
mostrar sin tocar tus proyectos) y `--port <p>` / `--data-dir <ruta>` para
cambiar los valores por defecto. Detén con **`Detener Babel's Hoard.cmd`**.

## Arquitectura

Backend FastAPI + SQLite (WAL, FTS5), frontend React 19 + Vite, un adaptador
MCP por stdio, y un trabajador en segundo plano para el indexado de larga
duración. Detalles, modelo de datos y las decisiones de análisis estático
menos obvias: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tests

```
.venv/bin/python -m pytest -q
```

**48 tests, ~12-14 s, sin red por defecto** (una instalación de docset
contra el espejo real de DevDocs se verificó a mano, no en CI): detección y
sondeo de entornos, indexado de Python contra paquetes reales instalados
(httpx, la stdlib) y un paquete de prueba con dos versiones creado a
propósito (`fakelib` 1.x/2.x, que modela una migración al estilo pandas de
`append` a `concat`), cada regla del comprobador (incluido el caso límite
de resolución de alias que antes era un falso positivo), búsqueda FTS5,
instalación de un docset desde un fixture, indexado de carpetas markdown,
indexado JS/TS contra un paquete `.d.ts` de prueba, la API HTTP completa y
su protección contra ataques desde el navegador mediante el `TestClient`
de FastAPI, y el adaptador MCP manejado **a través del protocolo stdio real
de MCP** contra una instancia real de la app en marcha (no importando sus
funciones).

`npm run build` en `frontend/` pasa sin errores de TypeScript.

## Privacidad y límites

Solo escucha en `127.0.0.1`, sin telemetría. Las únicas dos herramientas
que tocan la red son `docs_catalog` y `docs_install_docset` (el espejo de
DevDocs); todo lo demás - sondear intérpretes, indexar paquetes, buscar,
comprobar código - es enteramente local. El código fuente se lee para
construir el índice (rutas, firmas, docstrings), pero el código del
proyecto del usuario nunca se ejecuta, solo se analiza de forma estática.
