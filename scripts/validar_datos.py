"""Verificación completa de hechos y manifiesto del dataset reproducible."""
import hashlib
import json
from importlib.metadata import version

from psycopg import sql

from config.database import DATABASES, ROOT
from core.database import conectar_bd, comparar_resultados, obtener_esquema_bd
from scripts.generar_datos import COLUMNAS, SEMILLA, VERSION, generar


def validar_datos(password, user=None):
    esperados = generar()
    manifiesto = {'version': VERSION, 'semilla': SEMILLA, 'faker': version('faker'),
                  'locale': 'es_ES', 'bases': {}, 'archivos_sha256': {}}
    for nivel, tablas in esperados.items():
        _, metricas = obtener_esquema_bd(nivel, password, user=user)
        resumen = {'nombre': DATABASES[nivel], 'metricas': metricas, 'filas': {}}
        with conectar_bd(nivel, password, user=user) as conn:
            with conn.cursor() as cur:
                for tabla, filas in tablas:
                    columnas = COLUMNAS[nivel][tabla].split(',')
                    cur.execute(sql.SQL('SELECT {} FROM {}').format(
                        sql.SQL(',').join(map(sql.Identifier, columnas)), sql.Identifier('public', tabla)))
                    actuales = cur.fetchall()
                    if not comparar_resultados(columnas, actuales, columnas, filas):
                        raise RuntimeError(f'{nivel}.{tabla}: datos distintos al dataset {VERSION}.')
                    resumen['filas'][tabla] = len(actuales)
        manifiesto['bases'][nivel] = resumen
    archivos = ['database/postgresql/esquema_bajo.sql', 'database/postgresql/esquema_alto.sql',
                'scripts/generar_datos.py', 'datasets/preguntas_postgresql.json']
    for archivo in archivos:
        manifiesto['archivos_sha256'][archivo] = hashlib.sha256((ROOT / archivo).read_bytes()).hexdigest()
    # Se escribe después de validar ambas bases, nunca en las bases de prueba.
    destino = ROOT / 'results' / 'dataset_ventas_v2.json'
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(manifiesto, ensure_ascii=False, indent=2)+'\n')
    print(f'Dataset completo verificado; manifiesto: {destino}')
    return manifiesto
