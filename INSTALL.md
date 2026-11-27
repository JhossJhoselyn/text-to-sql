# Instalación paso a paso

## 1. Preparar el equipo

El procedimiento se comprobó en Ubuntu 24.04 y WSL2 con Python 3.12. En Windows, abre la terminal Ubuntu de WSL2 y activa la integración de esa distribución en Docker Desktop. Instala Docker Engine con Compose v2 o Docker Desktop y comprueba que estén en ejecución.

Instala las herramientas del sistema en Ubuntu o WSL.

```bash
sudo apt update
sudo apt install -y git python3.12-venv python3.12-dev build-essential cmake
python3.12 --version
docker compose version
docker info
```

Los modelos ocupan unos 3 GB, además del entorno Python, la compilación y los datos de PostgreSQL. La inferencia está configurada para CPU. La instalación requiere acceso a internet.

## 2. Clonar el repositorio

```bash
git clone https://github.com/JhossJhoselyn/text-to-sql.git text-to-sql
cd text-to-sql
```

El repositorio utiliza Git normal. Los modelos se descargan por separado después de instalar las dependencias Python; no necesitas Git LFS.

## 3. Crear el entorno Python

Ejecuta los siguientes comandos en la raíz del clon.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip check
```

La compilación de `llama-cpp-python` puede tardar varios minutos. Si falla, comprueba que están instalados CMake, el compilador C++ y los encabezados de Python 3.12. Activa `.venv` en cada terminal desde la que ejecutes comandos Python del proyecto.

### Descargar los modelos

Con el entorno Python activo, ejecuta lo siguiente.

```bash
python -m scripts.descargar_modelo
ls -lh models/*.gguf
```

El script descarga ambas cuantizaciones del [repositorio oficial de Qwen](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/tree/main): `qwen2.5-coder-1.5b-instruct-q4_k_m.gguf` (aproximadamente 1,1 GB) y `qwen2.5-coder-1.5b-instruct-q8_0.gguf` (aproximadamente 1,9 GB). Para descargar una sola, usa `python -m scripts.descargar_modelo Q4_K_M` o `python -m scripts.descargar_modelo Q8_0`. El script conserva los archivos ya descargados. `models/` está excluida de Git.

## 4. Crear la configuración local

```bash
cp infra/.env.example infra/.env
```

Edita `infra/.env` y sustituye las dos contraseñas de ejemplo por valores propios.

```dotenv
POSTGRES_HOST=localhost
POSTGRES_ADMIN_USER=postgres
POSTGRES_ADMIN_PASSWORD=contraseña-administradora-propia
POSTGRES_PORT=5432
TEXT_TO_SQL_READER_USER=text_to_sql_lector
TEXT_TO_SQL_READER_PASSWORD=contraseña-lectora-propia
```

La preparación crea el rol `text_to_sql_lector`. Flask utiliza el rol lector y las credenciales se cargan en el servidor. `infra/.env` está excluido de Git. Reinicia Flask después de modificarlo.

Si el puerto 5432 está ocupado, cambia `POSTGRES_PORT`, por ejemplo a `55432`. Para ejecutar dos instalaciones en el mismo equipo, asigna además un nombre de proyecto Compose diferente en cada `.env`.

```dotenv
COMPOSE_PROJECT_NAME=text-to-sql-pruebas
POSTGRES_PORT=55432
```

## 5. Inicializar PostgreSQL y el dataset

```bash
python -m scripts.preparar_bd
```

El comando inicia PostgreSQL con Compose, espera su disponibilidad, crea las bases `text_to_sql_baja_v2` y `text_to_sql_alta_v2`, carga los datos sintéticos y configura los permisos del rol lector. Después compara las 40 parejas de consultas SQL de referencia.

```bash
docker compose --env-file infra/.env -f infra/docker-compose.yml ps
python -m scripts.verificar_bd
```

La base baja debe tener 7 tablas y 6 relaciones, y la alta 12 tablas y 13 relaciones. Ambas contienen 3.000 clientes. Los modelos pueden generar SQL incorrecto aunque el dataset y las consultas de referencia estén verificados.

Si reutilizas un volumen existente, utiliza la contraseña administradora con la que fue creado. Cambiar `.env` no cambia automáticamente la contraseña de ese servidor. La preparación conserva los datos existentes y los valida, y rechaza cargas parciales o esquemas inesperados.

## 6. Iniciar la web

```bash
python -m web
```

Abre [http://localhost:5000](http://localhost:5000). Verás Demostración, Experimentos y Resultados. El selector Claro/Oscuro guarda tu preferencia en el navegador.

Si el puerto está ocupado, ejecuta `python -m web --port 5001` y abre `http://localhost:5001`. El servidor escucha en la dirección local y trabaja con un único proceso. Evita iniciar varias instancias contra los mismos resultados al ejecutar experimentos.

## 7. Comprobar el funcionamiento

En Demostración, selecciona la pregunta P01, el esquema bajo y una cuantización. Pulsa Generar SQL y consultar. Compara los datos obtenidos con los esperados. La referencia debe mostrar 3.000 clientes. Revisa también Explorar el esquema.

En Experimentos, configura un piloto de una pregunta y una repetición para comprobar el progreso. En Resultados, selecciona la corrida y descarga CSV, JSON o Informe. Para repetir las comprobaciones automatizadas de Python, usa lo siguiente.

```bash
python -m unittest discover -s tests -v
```

Las pruebas de navegador requieren Node.js 22 y una web en ejecución. Abre otra terminal en el clon y ejecuta lo siguiente.

```bash
npm ci
npx playwright install chromium
npm run test:e2e
```

Las pruebas guardan corridas `e2e_*`. Para probar en otra URL, utiliza `TEXTSQL_E2E_URL=http://127.0.0.1:5001 npm run test:e2e`. Si falta alguna biblioteca del sistema para Chromium, usa `npx playwright install --with-deps chromium`.

## 8. Arranques posteriores y parada

En una nueva terminal, entra en la carpeta clonada y ejecuta lo siguiente.

```bash
source .venv/bin/activate
docker compose --env-file infra/.env -f infra/docker-compose.yml up -d --wait
python -m web
```

Detén Flask con Ctrl+C. Para detener PostgreSQL conservando sus datos, usa lo siguiente.

```bash
docker compose --env-file infra/.env -f infra/docker-compose.yml down
```

`down -v` elimina permanentemente las bases del volumen. Para respaldar los experimentos, detén Flask y copia `results/`. Los modelos se recuperan con `python -m scripts.descargar_modelo`, y un equipo nuevo reconstruye sus bases con `scripts.preparar_bd`.

## 9. Publicar cambios en GitHub

```bash
git status
git add .
git commit -m "Describe el cambio realizado"
git push --set-upstream origin main
```

`models/`, `.venv/`, credenciales y resultados generados se excluyen de Git. El push utiliza Git normal y no transfiere los modelos. En cada equipo nuevo, descárgalos con el procedimiento del paso 3.

`docs/` se mantiene localmente y está excluida de Git. Las instrucciones de instalación que necesita un clon están en `README.md`, `INSTALL.md` e `infra/README.md`.
