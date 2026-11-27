"""Configuración compartida por la aplicación y las utilidades de preparación."""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / 'infra' / '.env')
HOST = os.getenv('POSTGRES_HOST', 'localhost')
PORT = int(os.getenv('POSTGRES_PORT', '5432'))
USER = os.getenv('POSTGRES_ADMIN_USER', 'postgres')
READER_USER = 'text_to_sql_lector'
DATABASES = {'baja': 'text_to_sql_baja_v2', 'alta': 'text_to_sql_alta_v2'}


def obtener_credenciales_lector() -> tuple[str, str]:
    """Lee las credenciales del servidor sin usar estado del navegador."""
    user = os.getenv('TEXT_TO_SQL_READER_USER', READER_USER).strip()
    password = os.getenv('TEXT_TO_SQL_READER_PASSWORD', '')
    if not user or not password:
        raise ValueError(
            'Configura TEXT_TO_SQL_READER_USER y TEXT_TO_SQL_READER_PASSWORD '
            'en infra/.env y reinicia la aplicación.'
        )
    return user, password


def conectar_bd(complejidad, password):
    """Abre una conexión administradora para preparar datos sintéticos."""
    return psycopg.connect(host=HOST, port=PORT, user=USER, password=password,
                           dbname=DATABASES[complejidad], connect_timeout=5)
