# PostgreSQL local

Crea `infra/.env` desde `.env.example` y configura las dos contraseñas. Desde la raíz del proyecto, ejecuta `python -m scripts.preparar_bd` con el entorno virtual activo. El script arranca PostgreSQL, crea las bases ausentes, configura el rol `text_to_sql_lector`, carga los datos sintéticos y comprueba las consultas de referencia.

Puedes volver a ejecutar la preparación con el mismo volumen. Si ya hay datos, los valida y conserva. Si el volumen fue creado con una contraseña administradora anterior, usa esa misma contraseña para conectarte.

| Variable | Uso |
| --- | --- |
| `POSTGRES_HOST` | Dirección a la que se conecta Python. El valor inicial es `localhost`. |
| `POSTGRES_PORT` | Puerto local de PostgreSQL. El valor inicial es `5432`. |
| `POSTGRES_ADMIN_USER` y `POSTGRES_ADMIN_PASSWORD` | Credenciales para preparar los datos. |
| `TEXT_TO_SQL_READER_USER` y `TEXT_TO_SQL_READER_PASSWORD` | Credenciales para consultas. El script prepara `text_to_sql_lector`. |
| `COMPOSE_PROJECT_NAME` | Nombre opcional para separar contenedores y volúmenes de dos instalaciones. Usa también un puerto distinto. |

Flask y PostgreSQL escuchan localmente. Para abrir el cliente de PostgreSQL, usa `localhost`, el puerto configurado y la base `text_to_sql_baja_v2` o `text_to_sql_alta_v2`.

```bash
docker compose --env-file infra/.env -f infra/docker-compose.yml ps
docker compose --env-file infra/.env -f infra/docker-compose.yml logs -f postgres
docker compose --env-file infra/.env -f infra/docker-compose.yml down
```

`down` conserva el volumen. `down -v` lo elimina permanentemente. Consulta la [guía de instalación](../README.md) para configurar Python y descargar los modelos.
