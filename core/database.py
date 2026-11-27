"""Acceso experimental PostgreSQL y comparacion de resultados."""
from __future__ import annotations

import re
import time
from collections import Counter
from decimal import Decimal

import psycopg

from config.database import DATABASES, HOST, PORT, READER_USER as USER
from core.sql_validator import validate_sql

MAX_FILAS = 100_000  # Si se supera, no se evalua una muestra parcial.
_PG_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')


class LimiteResultadosError(ValueError):
    """Error cuando una consulta supera el limite de filas permitido."""


class PrivilegiosExcesivosError(PermissionError):
    """Error cuando se intenta usar un rol administrativo o con escritura."""


def validar_nombre_usuario(user: str) -> str:
    """Valida que el nombre de usuario PostgreSQL sea un identificador seguro."""
    cleaned = (user or '').strip()
    if not cleaned or not _PG_IDENT_RE.match(cleaned):
        raise ValueError(
            'Nombre de usuario invalido. Usa letras, digitos o guion bajo (max. 63 caracteres).'
        )
    return cleaned


def conectar_bd(nivel: str, password: str, user: str | None = None):
    """Abre una conexion de solo lectura a una base autorizada."""
    if nivel not in DATABASES:
        raise ValueError('Nivel de complejidad invalido.')
    if not password:
        raise ValueError('La contrasena no puede estar vacia.')
    db_user = validar_nombre_usuario(user or USER)
    # TODO(security): Habilitar sslmode='verify-full' con certificados mTLS
    # cuando PostgreSQL se despliegue fuera del contenedor Docker local.
    conn = psycopg.connect(
        host=HOST,
        port=PORT,
        user=db_user,
        password=password,
        dbname=DATABASES[nivel],
        connect_timeout=5,
        options='-c search_path=public -c default_transaction_read_only=on -c statement_timeout=10000',
    )
    conn.read_only = True
    return conn


def verificar_acceso_bd(user: str, password: str) -> dict:
    """Valida credenciales, principio de minimo privilegio y esquemas en ambas bases."""
    db_user = validar_nombre_usuario(user)
    if not password:
        raise ValueError('Introduce la contrasena para verificar la conexion.')

    resumen = {
        'user': db_user,
        'host': HOST,
        'port': PORT,
        'read_only': True,
        'bases': {},
    }

    for nivel, dbname in DATABASES.items():
        with conectar_bd(nivel, password, user=db_user) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    'SELECT r.rolsuper, r.rolcreaterole, r.rolcreatedb, '
                    "current_setting('transaction_read_only') "
                    'FROM pg_catalog.pg_roles r '
                    'WHERE r.rolname = current_user'
                )
                row = cur.fetchone()
                if row is None:
                    raise PermissionError('No se pudo verificar el rol activo en PostgreSQL.')
                rolsuper, rolcreaterole, rolcreatedb, tx_ro = row
                if rolsuper or rolcreaterole or rolcreatedb:
                    raise PrivilegiosExcesivosError(
                        'Por seguridad no se permiten cuentas administradoras (superuser/createrole/createdb). '
                        'Utiliza un usuario lector con privilegios minimos (por ejemplo: text_to_sql_lector).'
                    )
                if str(tx_ro).lower() != 'on':
                    raise PermissionError('La sesion PostgreSQL no esta en modo de solo lectura.')

        esquema, metricas, detalle_tablas = obtener_tablas_y_esquema_bd(nivel, password, user=db_user)
        if not esquema or metricas['tablas'] == 0:
            raise ValueError(f'La base {dbname} no contiene tablas publicas accesibles.')
        resumen['bases'][nivel] = {
            'dbname': dbname,
            'metricas': metricas,
            'tablas': sorted(detalle_tablas.keys()),
        }

    return resumen


def obtener_tablas_y_esquema_bd(nivel: str, password: str, user: str | None = None):
    """Lee columnas, tipos y restricciones reales desde pg_catalog de forma determinista."""
    with conectar_bd(nivel, password, user=user) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.relname, a.attname,
                       pg_catalog.format_type(a.atttypid, a.atttypmod),
                       a.attnotnull
                FROM pg_catalog.pg_class c
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid
                WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
                  AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY c.relname, a.attnum
            """)
            columnas = cur.fetchall()
            cur.execute("""
                SELECT c.relname, k.contype,
                       pg_catalog.pg_get_constraintdef(k.oid)
                FROM pg_catalog.pg_constraint k
                JOIN pg_catalog.pg_class c ON c.oid = k.conrelid
                JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relkind IN ('r','p')
                  AND k.contype IN ('p','f','u')
                ORDER BY c.relname, k.contype, k.conname
            """)
            restricciones = cur.fetchall()

    tablas: dict[str, list[str]] = {}
    detalle: dict[str, dict] = {}
    for tabla, columna, tipo, obligatorio in columnas:
        tablas.setdefault(tabla, []).append(
            f'{columna} {tipo}' + (' NOT NULL' if obligatorio else '')
        )
        info = detalle.setdefault(tabla, {'columnas': [], 'restricciones': []})
        info['columnas'].append({
            'nombre': columna,
            'tipo': tipo,
            'obligatorio': bool(obligatorio),
        })

    for tabla, contype, definicion in restricciones:
        if tabla in tablas:
            tablas[tabla].append(definicion)
            detalle[tabla]['restricciones'].append({
                'tipo': contype,
                'definicion': definicion,
            })

    esquema = '\n\n'.join(
        f'CREATE TABLE {tabla} (\n  ' + ',\n  '.join(campos) + '\n);'
        for tabla, campos in tablas.items()
    )
    metricas = {
        'tablas': len(tablas),
        'columnas': len(columnas),
        'relaciones': sum(tipo == 'f' for _, tipo, _ in restricciones),
    }
    return esquema, metricas, detalle


def obtener_esquema_bd(nivel: str, password: str, user: str | None = None):
    """Lee columnas, tipos y restricciones reales desde pg_catalog."""
    esquema, metricas, _ = obtener_tablas_y_esquema_bd(nivel, password, user=user)
    return esquema, metricas


def ejecutar_sql_bd(
    sql: str,
    nivel: str,
    password: str,
    user: str | None = None,
    allowed_tables: set[str] | frozenset[str] | None = None,
):
    """Valida y ejecuta una sola sentencia SELECT/WITH en transaccion de solo lectura."""
    verdict = validate_sql(sql, allowed_tables=allowed_tables)
    if not verdict.ok:
        raise ValueError(verdict.error)

    consulta = verdict.sql
    with conectar_bd(nivel, password, user=user) as conn:
        with conn.cursor() as cur:
            inicio = time.perf_counter()
            # El protocolo preparado rechaza multiples sentencias SQL.
            cur.execute(consulta, prepare=True)
            if cur.description is None:
                raise ValueError('La consulta no devuelve una tabla de resultados.')
            columnas = [d.name for d in cur.description]
            if cur.rowcount > MAX_FILAS:
                raise LimiteResultadosError(f'Resultado superior a {MAX_FILAS} filas.')
            registros = cur.fetchall()
            if len(registros) > MAX_FILAS:
                raise LimiteResultadosError(f'Resultado superior a {MAX_FILAS} filas.')
            tiempo = time.perf_counter() - inicio
    return columnas, registros, tiempo


def _valor(v):
    # No confundir booleanos con numeros ni texto con fechas/numeros.
    if isinstance(v, bool):
        return ('bool', v)
    if isinstance(v, (int, float, Decimal)):
        d = Decimal(str(v))
        return ('numero', str(d) if not d.is_finite() else d)
    if isinstance(v, dict):
        return ('dict', tuple(sorted((k, _valor(x)) for k, x in v.items())))
    if isinstance(v, (list, tuple)):
        return ('secuencia', tuple(_valor(x) for x in v))
    return (type(v).__name__, v)


def comparar_resultados(columnas_a, filas_a, columnas_b, filas_b, orden=False):
    """Compara valores, posicion de columnas y duplicados, no sus alias."""
    if len(columnas_a) != len(columnas_b):
        return False
    a = [tuple(_valor(x) for x in fila) for fila in filas_a]
    b = [tuple(_valor(x) for x in fila) for fila in filas_b]
    return a == b if orden else Counter(a) == Counter(b)


def columnas_unicas(columnas):
    usadas = set()
    nuevas = []
    for columna in columnas:
        original = str(columna)
        nombre, numero = original, 2
        while nombre in usadas or (nombre != original and nombre in columnas):
            nombre = f'{original}_{numero}'
            numero += 1
        usadas.add(nombre)
        nuevas.append(nombre)
    return nuevas
