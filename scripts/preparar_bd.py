"""Prepara las bases del prototipo en el servicio PostgreSQL de Docker Compose."""
import json
import os
import subprocess
from getpass import getpass

from psycopg import sql

from config.database import DATABASES, ROOT, USER, conectar_bd
from core.database import comparar_resultados, ejecutar_sql_bd
from scripts.generar_datos import COLUMNAS, cargar_datos
from scripts.validar_datos import validar_datos

COMPOSE = ['docker', 'compose', '--env-file', str(ROOT / 'infra/.env'),
           '-f', str(ROOT / 'infra/docker-compose.yml')]


def ejecutar_psql(database, consulta):
    """Ejecuta SQL por stdin dentro del contenedor, sin cliente PostgreSQL local."""
    result = subprocess.run(
        COMPOSE + ['exec', '-T', 'postgres', 'psql', '-X', '-U', USER,
                   '-d', database, '-v', 'ON_ERROR_STOP=1', '-At'],
        input=consulta, text=True, capture_output=True, check=False,
    )
    if result.returncode:
        # Las consultas pueden contener contraseñas: no imprimir el SQL ni stderr.
        raise RuntimeError(f'Falló psql en {database}; revisa el servicio y los permisos.')
    return result.stdout.strip()


def validar_gold(password, user=None):
    """Comprueba las parejas gold con las mismas conexiones de lectura que la UI."""
    casos = json.loads((ROOT / 'datasets/preguntas_postgresql.json').read_text())
    for caso in casos:
        baja = ejecutar_sql_bd(caso['baja'], 'baja', password, user=user)
        alta = ejecutar_sql_bd(caso['alta'], 'alta', password, user=user)
        if not comparar_resultados(*baja[:2], *alta[:2], orden=caso['orden']):
            raise RuntimeError(f"Resultados gold distintos: {caso['id']}")
        if not baja[1] or not alta[1]:
            raise RuntimeError(f"Gold sin filas: {caso['id']}")
        print(f"{caso['id']}: gold equivalente en ambos esquemas")


def main():
    admin_password = os.getenv('POSTGRES_ADMIN_PASSWORD') or getpass('Contraseña administradora: ')
    reader_password = os.getenv('TEXT_TO_SQL_READER_PASSWORD') or getpass('Contraseña del lector: ')
    if not reader_password:
        raise ValueError('La contraseña del lector no puede estar vacía.')
    subprocess.run(COMPOSE + ['up', '-d', '--wait', 'postgres'], check=True)

    # Crear solo bases ausentes; CREATE DATABASE debe ejecutarse fuera de transacciones.
    for database in DATABASES.values():
        existe = ejecutar_psql('postgres', f"SELECT 1 FROM pg_database WHERE datname = '{database}';")
        if existe != '1':
            ejecutar_psql('postgres', f'CREATE DATABASE "{database}";')

    ocupadas = []
    for nivel, database in DATABASES.items():
        with conectar_bd(nivel, admin_password) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                tablas = {row[0] for row in cur.fetchall()}
                esperadas = set(COLUMNAS[nivel])
                if tablas and tablas != esperadas:
                    raise RuntimeError(f'{database}: esquema existente inesperado; no se modifica.')
                if not tablas:
                    archivo = 'esquema_bajo.sql' if nivel == 'baja' else 'esquema_alto.sql'
                    cur.execute((ROOT / 'database/postgresql' / archivo).read_text())
                for tabla in esperadas:
                    cur.execute(sql.SQL('SELECT EXISTS (SELECT 1 FROM public.{})').format(sql.Identifier(tabla)))
                    ocupadas.append(cur.fetchone()[0])
    if any(ocupadas) and not all(ocupadas):
        raise RuntimeError('Carga parcial existente: revisa los datos antes de continuar.')
    if not any(ocupadas):
        cargar_datos(admin_password)
    else:
        print('Datos existentes: se conservan y se validan las consultas gold.')

    # Componer identificadores y contraseña con Psycopg, nunca con interpolación shell.
    with conectar_bd('baja', admin_password) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'text_to_sql_lector'")
            if cur.fetchone() is None:
                cur.execute('CREATE ROLE text_to_sql_lector LOGIN')
            cur.execute(sql.SQL('ALTER ROLE text_to_sql_lector WITH LOGIN NOSUPERUSER NOCREATEDB '
                                'NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}').format(sql.Literal(reader_password)))
            cur.execute('ALTER ROLE text_to_sql_lector SET default_transaction_read_only = on')
            cur.execute("ALTER ROLE text_to_sql_lector SET statement_timeout = '10s'")
    for nivel, database in DATABASES.items():
        with conectar_bd(nivel, admin_password) as conn:
            with conn.cursor() as cur:
                cur.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO text_to_sql_lector').format(sql.Identifier(database)))
                cur.execute('GRANT USAGE ON SCHEMA public TO text_to_sql_lector')
                cur.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public TO text_to_sql_lector')
    validar_datos(reader_password)
    validar_gold(reader_password)
    print('Base preparada. Ejecuta: python -m web')


if __name__ == '__main__':
    main()
