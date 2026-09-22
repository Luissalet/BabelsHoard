# Babel's Hoard
### ¿Qué dice de verdad la versión que tienes instalada?
**Un índice local y exacto de las API de los paquetes instalados en tus proyectos, y un comprobador que detecta API inventadas o mal usadas en el código que acaba de escribir un modelo, sin ejecutar nunca ese código.**

[English](README.md) · [Ejecutar en local](#ejecutar-en-local-en-windows) · [Conectar una IA](docs/MCP.md) · [Portfolio](https://luissalet.github.io/Portfolio/#projects)

![Búsqueda: «send a get request» devuelve httpx.Client.get con la firma de httpx 0.28.1 instalado](docs/media/search.png)
*Aplicación real, datos de demostración: el intérprete del propio Babel con httpx, pydantic, fastapi, griffe y el módulo json de la biblioteca estándar indexados.*

## Por qué

Los datos de entrenamiento de un modelo de código mezclan versiones de las
librerías. Escribe `df.append(...)` para pandas 2.x, pasa un argumento que
cambió de nombre, importa algo que se movió de módulo o llama a
`response.jsonify()` porque suena bien. Buscar en la web es lento y no
distingue versiones. La verdad ya está en el disco: el `.venv` y el
`node_modules` del propio proyecto. Babel los lee de forma estática,
responde con la firma real y comprueba fragmentos de código contra ellos.

El comprobador está pensado para que un modelo que no puede verificarlo se
fíe de él: **solo informa de un error cuando puede demostrarlo.** Un nombre
que falta en un módulo que hace `import *` de una extensión compilada (`os`,
`socket`), que rellena su espacio de nombres con `globals()` (`re`,
`hashlib`) o que resuelve nombres en `__getattr__` nunca se da como error; el
código protegido con `try/except ImportError` o `hasattr` no se toca. Lo que
no puede demostrar se cuenta como `unchecked` (sin verificar).

![Comprobar código: response.jsonify() y client.post(..., retry=3) marcados contra httpx 0.28.1, con las líneas afectadas](docs/media/check-code.png)
*Aplicación real, datos de demostración. El fragmento se analiza, nunca se ejecuta; `jsonify` se detecta gracias al tipo que devuelve `client.get` y a la variable de `with ... as client`.*

## Qué está implementado

| Área | Disponible ahora | Límite |
| --- | --- | --- |
| Indexado de Python | Indexado estático (griffe sobre el código fuente y los stubs `.pyi`; el código del proyecto nunca se ejecuta) de las distribuciones y los módulos de la biblioteca estándar de cualquier intérprete registrado, cuando hace falta por primera vez y en caché por entorno + paquete + versión. En anchura, para que la API pública se indexe primero; cada objeto se expande una sola vez y sus otras rutas públicas apuntan a él; se resuelven clases base y reexportaciones de otros paquetes; se registra qué módulos y clases se conocen por completo (espacios de nombres dinámicos, límites, módulos compilados) | Límites por paquete: 15.000 entradas, 1.500 módulos analizados (sin las baterías de tests), 600 módulos de otros paquetes; una librería que los supera queda como `partial` y sus espacios de nombres incompletos nunca generan errores. Las extensiones compiladas sin stubs se registran como tales, sin miembros. No se indexan los módulos propios del proyecto que no estén instalados |
| Seguimiento de versiones | El sondeo del intérprete se reutiliza hasta que cambia su site-packages; tras una actualización, la siguiente consulta indexa la versión nueva y marca la anterior como `superseded` (solo se muestra como «otras versiones») | La detección se basa en la fecha de modificación de las carpetas de site-packages; las instalaciones editables que cambian el código sin reinstalar conservan lo indexado hasta reindexar |
| Comprobación de código (`api_check_code`) | Módulos y atributos inexistentes, argumentos por nombre inesperados, parámetros solo posicionales pasados por nombre, demasiados posicionales, obligatorios que faltan y API obsoletas, con sugerencias. Sigue los valores a través de importaciones, asignaciones, anotaciones, tipos de retorno, métodos que devuelven `Self`, `with`/`async with` y `await`; distingue receptores (instancia, clase, estático, sin enlazar) y constructores; las sobrecargas se comprueban contra su contrato | Errores solo en espacios de nombres completos; las clases con `__getattr__` o `setattr(self, nombre)` y los módulos perezosos dan avisos; los tipos desconocidos, metaclases propias, `__new__`, decoradores desconocidos y el código protegido quedan sin verificar. TypeScript: solo importaciones y reexportaciones con nombre (v1) |
| Consulta (`api_lookup`) | Firma, parámetros (tipo, valor por defecto, obligatorio, tipo de paso, descripción), tipo de retorno, resumen, los primeros 1.500 caracteres del docstring, miembros, archivo:línea y versión de la librería; `found: false` con los nombres reales más parecidos y si la ausencia es segura | Paquetes de Python y paquetes npm con declaraciones de tipos |
| Indexado de JS/TS | API del compilador de TypeScript sobre las declaraciones del paquete (`types`/`typings`, `exports[...].types`, `index.d.ts`, `@types/<nombre>`): exportaciones, un nivel de miembros, JSDoc y `@deprecated`; se indexa al primer uso | Requiere Node.js; como máximo 500 exportaciones por paquete (después, `partial`) |
| Búsqueda | FTS5 de SQLite con pesos bm25 (nombre, ruta, firma, resumen, documentación), tokens que entienden identificadores, filtros, un resultado por definición y sugerencias por trigramas | Léxica, sin embeddings |
| Documentación sin conexión | Catálogo e instalación de DevDocs (HTML convertido a Markdown y dividido por anclas) como tarea en segundo plano | Necesita red, solo cuando lo pide el usuario o el modelo; el conversor es pequeño, no un renderizador HTML completo |
| Carpetas de Markdown | `.md`/`.mdx`/`.rst`/`.txt` divididos por encabezado (respetando bloques de código y subrayados de rst) | Se omiten carpetas de dependencias, control de versiones y compilación; 2.000 archivos y 2 MB por archivo |
| Auditoría del asistente | Cada llamada a `/api/agent/*` (herramienta, resumen de argumentos, duración, resultado) en «Actividad del asistente»; la interfaz usa sus propios endpoints, así que solo aparecen las llamadas del modelo | Solo local |
| Interfaz | Búsqueda, panel de símbolo, Librerías (paquetes instalados con su estado de indexado), Comprobar código (números de línea, avisos bajo cada línea), Docsets, Actividad del asistente y Ajustes; tema claro/oscuro, inglés/español | Un solo usuario, navegador local |

## Conectarlo a Faustus

La aplicación se declara con [`faustus-plugin.json`](faustus-plugin.json).
Arranca Babel's Hoard y en Faustus ve a **Conectores → Apps cercanas →
Añadir**. Faustus lanza el adaptador stdio `babels_hoard/mcp_server.py`, que
habla con la aplicación por la interfaz local.

| Herramienta MCP | Qué hace | Solo lectura |
| --- | --- | --- |
| `docs_libraries` | Librerías indexadas con versiones, entornos (y cuál es el predeterminado) y tareas recientes | sí |
| `docs_search` | Búsqueda ordenada en API, docsets y markdown | sí |
| `api_lookup` | Firma, parámetros y documentación exactos de un símbolo instalado | sí (solo escribe la caché local del índice) |
| `api_check_code` | API inventadas, eliminadas o mal usadas en un fragmento | sí (solo escribe la caché local del índice) |
| `docs_read` | Leer por partes un docstring o una sección por su id | sí |
| `docs_add_environment` | Registrar una carpeta de proyecto, un intérprete o un node_modules | no |
| `docs_catalog` | Docsets descargables (red) | sí |
| `docs_install_docset` | Descargar e indexar un docset en segundo plano (red) | no |
| `docs_index_folder` | Indexar una carpeta de documentación markdown | no |

Cada descripción termina con palabras clave en inglés y en español para que
Faustus encuentre la herramienta. Formas de argumentos y resultados, límites
y ejemplos: [docs/MCP.md](docs/MCP.md). La skill para el agente está en
[skills/version-exact-apis/SKILL.md](skills/version-exact-apis/SKILL.md).

Funciona con cualquier cliente MCP por stdio:

```json
{
  "mcpServers": {
    "babels-hoard": {
      "command": "C:\\ruta\\a\\Babel's Hoard\\.venv\\Scripts\\python.exe",
      "args": ["C:\\ruta\\a\\Babel's Hoard\\babels_hoard\\mcp_server.py"],
      "env": { "BABEL_URL": "http://127.0.0.1:8811" }
    }
  }
}
```

## Ejecutar en local en Windows

Haz doble clic en **`Iniciar Babel's Hoard.cmd`**. La primera vez,
`scripts/start.ps1` busca Python 3.13 (lanzador `py`, `C:\Python313` y
después el PATH; acepta 3.11 o superior), crea `.venv`, instala
`requirements-lock.txt`, compila la interfaz web y la sonda de TypeScript si
tienes Node.js, arranca la aplicación desde la carpeta del repositorio y
abre <http://127.0.0.1:8811> en cuanto `/api/health` responde. Las
siguientes veces solo reinstala si ha cambiado el archivo de bloqueo.
**`Detener Babel's Hoard.cmd`** la para.

Pasos a mano:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-lock.txt
cd frontend; npm ci; npm run build; cd ..
.venv\Scripts\python -m babels_hoard
```

Opciones: `--port <p>` (8811 por defecto), `--data-dir <ruta>` (por defecto
`data\`, o `BABEL_DATA_DIR`), `--no-browser` y `--demo`, que usa
`data-demo\` e indexa unos cuantos paquetes del intérprete del propio Babel
para probar la interfaz sin tocar tus proyectos.

## Arquitectura

FastAPI + SQLite (WAL, una conexión por hilo, FTS5), un hilo de tareas en
segundo plano, griffe para el análisis estático de Python, la API del
compilador de TypeScript para las declaraciones, una interfaz React 19 +
Vite y un adaptador MCP por stdio independiente. Módulos, modelo de datos y
las decisiones detrás del comprobador:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Tests

```powershell
.venv\Scripts\python -m pytest -q
```

**108 tests, entre 40 y 60 s en un contenedor compartido de 2 CPU, sin red.** Cubren: la
protección frente al navegador y el confinamiento de archivos estáticos
(intentos de salir de la carpeta), la forma de los errores, las conexiones
por hilo y que `/api/health` responda mientras corre una herramienta larga;
el indexado de paquetes reales instalados (httpx, pydantic, fastapi, PyJWT,
python-dotenv y la biblioteca estándar) y de dos paquetes de prueba:
`fakelib` 1.x/2.x (una API eliminada entre versiones) y `edgelib` (cada caso
de espacio de nombres dinámico, decorador, metaclase, sobrecarga y gestor de
contexto que el comprobador debe resolver bien); una actualización simulada
en un venv desechable; los nombres de la biblioteca estándar que antes daban
falsos positivos (`os.getcwd`, `socket.AF_INET`, `re.IGNORECASE`,
`hashlib.sha256`, `sqlite3.connect`); búsqueda, docsets, markdown e indexado
de JS/TS (requiere Node.js); y el adaptador MCP lanzado con el **protocolo
stdio real** contra la aplicación en marcha, incluidas palabras clave,
anotaciones, ids que se pueden reutilizar y el paso de errores.

Banco de falsos positivos: `scripts/check_corpus.py` pasa el comprobador
por el código de paquetes instalados, que funciona, así que cualquier error
que marque es sospechoso. En 300 archivos tomados de starlette, fastapi,
httpx, uvicorn, mcp, anyio, pydantic-settings, click, jsonschema, griffe,
pandas y requests no marca ningún error ni aviso (en la muestra de
pandas/requests: 4.954 comprobaciones verificadas y 11.551 sin verificar).
Las cuatro clases de falsos positivos que encontró están corregidas y
cubiertas por tests.

`npm run build` en `frontend/` termina sin errores de TypeScript. El primer
arranque de `scripts/start.ps1` (venv, instalación del bloqueo, compilación
de la interfaz, arranque, espera a `/api/health` y detección de una
instancia ya en marcha) se ha ejecutado con PowerShell 7 en Linux; el flujo
de CI define además un trabajo `windows-latest` que ejecuta `start.ps1` y
`stop.ps1`, que aún no se ha ejecutado porque el repositorio no se ha
subido.

## Privacidad y límites

Solo escucha en `127.0.0.1`; rechaza peticiones con un `Host` ajeno,
escrituras desde otros sitios y lecturas de la API desde otros sitios. Sin
telemetría. Solo el catálogo y la instalación de docsets usan la red (el
espejo público de DevDocs; su contenido mantiene sus licencias originales).
Babel ejecuta los intérpretes que registras únicamente para lanzar su
script de sondeo, que solo usa la biblioteca estándar, y solo si el archivo
se llama como un intérprete de Python; lee el código instalado y los
archivos de declaraciones, y nunca importa ni ejecuta el código del
proyecto ni los fragmentos que comprueba.
