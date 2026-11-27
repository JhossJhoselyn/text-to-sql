"""Adaptadores de la interfaz HTTP a los servicios experimentales existentes."""
from __future__ import annotations

from dataclasses import asdict

from config.database import obtener_credenciales_lector
from core.agent import run_agent
from core.database import obtener_tablas_y_esquema_bd, verificar_acceso_bd
from core.model_adapter import MODEL_BASE_NAME, listar_cuantizaciones_disponibles
from core.persistence import cargar_banco_preguntas, ensure_demo_run, next_demo_repetition, record_attempt


def available_quants():
    return [q['quantization_id'] for q in listar_cuantizaciones_disponibles(calcular_hash=False) if q['exists']]


def credentials():
    user, password = obtener_credenciales_lector()
    verificar_acceso_bd(user, password)
    return user, password


def validate_selection(data):
    if data.get('schema') not in ('baja', 'alta'):
        raise ValueError('Selecciona un esquema válido.')
    if data.get('quantization') not in available_quants():
        raise ValueError('Selecciona una cuantización disponible en models/.')


def run_demo(data):
    user, password = credentials()
    reference = next((c for c in cargar_banco_preguntas() if c['id'] == data.get('case_id')), None)
    question = reference['pregunta'] if reference else data.get('question', '').strip()
    schema, quant = data['schema'], data['quantization']
    save = data.get('save', True)
    run_id, repetition = 'demo_strict', 1
    if save:
        run_id = ensure_demo_run('strict', {'tipo': 'demo_interactiva', 'mode': 'strict', 'model_base': MODEL_BASE_NAME})
        repetition = next_demo_repetition(run_id, reference['id'] if reference else 'libre', quant, schema)
    result = run_agent(
        question=question, schema_id=schema, quantization_id=quant,
        db_user=user, db_password=password, mode='strict',
        case_id=reference['id'] if reference else 'libre', run_id=run_id, repetition=repetition,
        gold_sql=reference[schema] if reference else None,
        require_order=bool(reference['orden']) if reference else False,
    )
    if save:
        record_attempt(result.trace)
    payload = asdict(result)
    # La comparación de exactitud usa todas las filas; la vista limita su tamaño.
    payload['rows'] = payload['rows'][:100]
    if payload['gold_rows_data'] is not None:
        payload['gold_rows_data'] = payload['gold_rows_data'][:100]
    return payload


def load_schema(schema):
    if schema not in ('baja', 'alta'):
        raise ValueError('Esquema no válido.')
    user, password = credentials()
    ddl, metrics, tables = obtener_tablas_y_esquema_bd(schema, password, user=user)
    return {'ddl': ddl, 'metrics': metrics, 'tables': tables}
