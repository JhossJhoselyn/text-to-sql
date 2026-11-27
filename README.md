# Text-to-SQL Lab

Aplicación web local que convierte preguntas en español en consultas SQL con Qwen2.5-Coder-1.5B-Instruct GGUF y PostgreSQL. Flask sirve la interfaz HTML con Tailwind, iconos SVG y temas claro y oscuro. El laboratorio utiliza un dataset de ventas sintético y permite comparar cuantizaciones y complejidad de esquemas.

## Instalación en otro equipo

La [guía INSTALL.md](INSTALL.md) explica la clonación y descarga de modelos, instalación de dependencias, configuración de credenciales, preparación de PostgreSQL y arranque de la web.

Entorno comprobado con Ubuntu 24.04 / WSL2, Python 3.12 y Docker Compose v2. La inferencia se ejecuta en CPU y requiere herramientas de compilación para instalar `llama-cpp-python`. Node.js 22 es opcional para trabajar en el frontend.

Con las herramientas del sistema instaladas, el recorrido es el siguiente.

```bash
git clone https://github.com/JhossJhoselyn/text-to-sql.git text-to-sql
cd text-to-sql
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m scripts.descargar_modelo
cp infra/.env.example infra/.env
```

Configura tus contraseñas en `infra/.env` antes de continuar.

```bash
python -m scripts.preparar_bd
python -m scripts.verificar_bd
python -m web
```

Abre [http://localhost:5000](http://localhost:5000). Para usar otro puerto, ejecuta `python -m web --port 5001`.

## Funciones

- **Demostración.** Preguntas del banco o preguntas libres. Muestra el SQL generado y sus filas junto a la referencia disponible. Los errores de ejecución conservan los datos esperados y se distinguen de una respuesta diferente.
- **Experimentos.** Compara cuantizaciones Q4_K_M y Q8_0 en esquemas de baja y alta complejidad. Configura preguntas, semilla y repeticiones. Permite detener y reanudar lotes.
- **Resultados.** Filtros, métricas, gráficos, inspección del SQL y descargas CSV, JSON y Markdown.

El modelo se evalúa con su primera generación. Puede producir consultas inválidas o resultados diferentes. Las tablas de la demostración muestran hasta 100 filas y la evaluación usa todas las filas. Los esquemas tienen 7 tablas/6 claves foráneas y 12 tablas/13 claves foráneas, con hechos equivalentes y 40 parejas de consultas de referencia.

## Organización

| Ruta | Contenido |
| --- | --- |
| `web/` | Aplicación Flask, plantillas, JavaScript y estilos Tailwind |
| `core/` | Agente, acceso de solo lectura, validador SQL, métricas y persistencia |
| `config/` | Configuración y prompts |
| `database/`, `datasets/` | Esquemas SQL y preguntas con referencias |
| `models/` | Modelos GGUF locales, excluidos de Git |
| `infra/`, `scripts/` | PostgreSQL con Compose y utilidades de preparación |
| `tests/` | Pruebas unitarias, HTTP y de navegador |
| `results/` | SQLite y manifiestos generados localmente |

`docs/` se conserva como material local y está excluida de Git. Las instrucciones necesarias para ejecutar un clon están en este README, `INSTALL.md` e `infra/README.md`.

## Pruebas y desarrollo

```bash
source .venv/bin/activate
python -m pip check
python -m unittest discover -s tests -v
python -m scripts.verificar_bd
```

La suite de Python usa dobles de PostgreSQL y del modelo. El verificador de base de datos comprueba los datos y las 40 parejas SQL contra PostgreSQL real.

Para editar estilos o ejecutar las pruebas del navegador, instala Node.js 22 y las dependencias del proyecto.

```bash
npm ci
npm run build:css
# Recompilación durante la edición
npm run watch:css
```

Con la web y PostgreSQL en marcha y los modelos descargados, ejecuta las pruebas de extremo a extremo desde otra terminal.

```bash
npx playwright install chromium
npm run test:e2e
```

Estas pruebas ejecutan consultas reales, detienen y reanudan un lote, verifican exportaciones y navegación móvil. Guardan una corrida `e2e_*`. `TEXTSQL_E2E_URL` cambia la URL del servidor y `TEXTSQL_CHROMIUM` permite utilizar otro ejecutable Chromium.

## Archivos locales y traslado de resultados

Git excluye `.env`, entornos virtuales, cachés, `docs/`, SQLite y resultados generados. La carpeta `models/` también está excluida. Después de instalar las dependencias, `python -m scripts.descargar_modelo` descarga ambas cuantizaciones desde el repositorio oficial de Qwen. El CSS compilado está incluido para arrancar sin instalar Node.

Para trasladar resultados ya obtenidos, detén Flask y copia `results/` al otro equipo. Conserva el manifiesto del dataset y utiliza las mismas versiones de dependencias y modelos. El estado temporal de tareas vive en memoria. Los lotes incompletos se reanudan desde sus manifiestos en SQLite. Ejecuta un solo proceso Flask para compartir el trabajador de inferencia.
