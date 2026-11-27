"""Orquestador de experimentos factoriales con manifiesto inmutable y reanudacion."""
from __future__ import annotations

import platform
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psutil

from core.agent import AgentMode, run_agent
from core.database import obtener_tablas_y_esquema_bd
from core.model_adapter import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_N_CTX,
    DEFAULT_N_THREADS,
    DEFAULT_PROMPT_VERSION,
    DEFAULT_TEMPERATURE,
    MODEL_BASE_NAME,
    cargar_prompt_plantilla,
    listar_cuantizaciones_disponibles,
)
from core.persistence import (
    DEFAULT_DB_PATH,
    cargar_banco_preguntas,
    cargar_metadatos_dataset,
    create_run,
    get_completed_keys,
    get_run,
    record_attempt,
    update_run_status,
)


def build_factorial_schedule(
    quantizations: list[str],
    schemas: list[str],
    cases: list[dict[str, Any]],
    repetitions: int = 1,
    seed: int = 42,
    shuffle: bool = True,
) -> list[dict[str, Any]]:
    """Genera el plan factorial determinista ordenado por cuantizacion para evitar recargas."""
    rng = random.Random(seed)
    tasks: list[dict[str, Any]] = []
    # Agrupamos por cuantizacion (un modelo en RAM a la vez) y barajamos los casos dentro
    # de cada bloque con semilla registrada para reducir sesgos de orden/calentamiento.
    for quant in quantizations:
        block = []
        for rep in range(1, repetitions + 1):
            for schema_id in schemas:
                for case in cases:
                    block.append({
                        'quantization_id': quant,
                        'schema_id': schema_id,
                        'case_id': case['id'],
                        'case': case,
                        'repetition': rep,
                    })
        if shuffle:
            rng.shuffle(block)
        tasks.extend(block)
    return tasks


def build_manifest(
    run_id: str,
    experiment_version: str,
    mode: AgentMode,
    quantizations: list[str],
    schemas: list[str],
    case_ids: list[str],
    repetitions: int,
    seed: int,
    shuffle: bool = True,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
) -> dict[str, Any]:
    """Construye un manifiesto auditable e inmutable con hashes y metadatos de entorno."""
    all_quants = {
        q['quantization_id']: q
        for q in listar_cuantizaciones_disponibles(calcular_hash=True)
    }
    _, _, prompt_sha256 = cargar_prompt_plantilla(prompt_version)
    dataset_meta = cargar_metadatos_dataset()
    mem = psutil.virtual_memory()

    return {
        'run_id': run_id,
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'experiment_version': experiment_version,
        'mode': mode,
        'model_base': MODEL_BASE_NAME,
        'quantizations': {q: all_quants.get(q, {}) for q in quantizations},
        'schemas': schemas,
        'case_ids': case_ids,
        'total_planned_attempts': len(quantizations) * len(schemas) * len(case_ids) * repetitions,
        'repetitions': repetitions,
        'seed': seed,
        'shuffle': shuffle,
        'inference_params': {
            'n_ctx': DEFAULT_N_CTX,
            'n_threads': DEFAULT_N_THREADS,
            'max_tokens': max_tokens,
            'temperature': temperature,
            'prompt_version': prompt_version,
            'prompt_sha256': prompt_sha256,
        },
        'dataset_metadata': dataset_meta,
        'environment': {
            'os': platform.platform(),
            'python': sys.version.split()[0],
            'cpu_count_logical': psutil.cpu_count(logical=True),
            'ram_total_gb': round(mem.total / (1024 ** 3), 2),
        },
    }


def run_factorial_experiment(
    run_id: str,
    experiment_version: str,
    mode: AgentMode,
    quantizations: list[str],
    schemas: list[str],
    cases: list[dict[str, Any]],
    repetitions: int,
    seed: int,
    db_user: str,
    db_password: str,
    shuffle: bool = True,
    resume_existing: bool = False,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    model_override=None,
) -> dict[str, Any]:
    """Ejecuta o reanuda un experimento factorial guardando cada intento atomicamente."""
    if resume_existing:
        existing = get_run(run_id, db_path=db_path)
        if existing is None:
            raise ValueError(f'No existe la corrida {run_id} para reanudar.')
        manifest = existing['manifest']
        update_run_status(run_id, 'running', db_path=db_path)
    else:
        manifest = build_manifest(
            run_id=run_id,
            experiment_version=experiment_version,
            mode=mode,
            quantizations=quantizations,
            schemas=schemas,
            case_ids=[c['id'] for c in cases],
            repetitions=repetitions,
            seed=seed,
            shuffle=shuffle,
        )
        create_run(
            run_id=run_id,
            experiment_version=experiment_version,
            mode=mode,
            manifest=manifest,
            status='running',
            db_path=db_path,
        )

    schedule = build_factorial_schedule(
        quantizations=quantizations,
        schemas=schemas,
        cases=cases,
        repetitions=repetitions,
        seed=seed,
        shuffle=shuffle,
    )
    completed_keys = get_completed_keys(run_id, db_path=db_path)

    # Cachear esquemas autorizados por nivel para no repetir introspeccion en cada caso.
    schema_cache: dict[str, tuple[str, dict, set[str]]] = {}
    for schema_id in schemas:
        ddl, metricas, detalle = obtener_tablas_y_esquema_bd(schema_id, db_password, user=db_user)
        schema_cache[schema_id] = (ddl, metricas, set(detalle.keys()))

    total = len(schedule)
    done = len(completed_keys)
    correct = 0
    errors = 0
    stopped = False

    for idx, task in enumerate(schedule, start=1):
        key = (task['case_id'], task['quantization_id'], task['schema_id'], int(task['repetition']))
        if key in completed_keys:
            continue

        if should_stop is not None and should_stop():
            stopped = True
            break

        case = task['case']
        schema_id = task['schema_id']
        quant_id = task['quantization_id']
        rep = int(task['repetition'])

        try:
            res = run_agent(
                question=case['pregunta'],
                schema_id=schema_id,
                quantization_id=quant_id,
                db_user=db_user,
                db_password=db_password,
                mode=mode,
                case_id=case['id'],
                run_id=run_id,
                repetition=rep,
                gold_sql=case[schema_id],
                require_order=bool(case.get('orden', False)),
                seed=seed,
                model_override=model_override,
                cached_schema=schema_cache[schema_id],
            )
            trace = res.trace
        except Exception as unexpected_err:
            # T12: una excepcion inesperada en una pregunta NO detiene el lote.
            from core.agent import AgentTrace
            import uuid
            trace = AgentTrace(
                attempt_id=str(uuid.uuid4()),
                run_id=run_id,
                case_id=case['id'],
                question=case['pregunta'],
                mode=mode,
                quantization_id=quant_id,
                schema_id=schema_id,
                repetition=rep,
                execution_error=f'Error inesperado en runner: {type(unexpected_err).__name__}',
                error_category='runner_error',
                execution_accuracy=0,
                status='error_runner',
            )
            trace.add_state('FAILED', trace.execution_error or 'error_runner')

        record_attempt(trace, db_path=db_path)
        completed_keys.add(key)
        done += 1
        if trace.execution_accuracy == 1:
            correct += 1
        elif trace.error_category not in ('ok', 'wrong_result'):
            errors += 1

        if on_progress is not None:
            on_progress({
                'done': done,
                'total': total,
                'step': idx,
                'correct_session': correct,
                'errors_session': errors,
                'last_case_id': case['id'],
                'last_quant': quant_id,
                'last_schema': schema_id,
                'last_status': trace.status,
                'last_accuracy': trace.execution_accuracy,
                'last_inference_ms': trace.inference_ms,
            })

    final_status = 'paused' if stopped else ('completed' if done >= total else 'partial')
    update_run_status(run_id, final_status, db_path=db_path)
    return {
        'run_id': run_id,
        'status': final_status,
        'completed': done,
        'total': total,
        'correct_session': correct,
        'errors_session': errors,
    }


def resume_run_from_manifest(
    run_id: str,
    db_user: str,
    db_password: str,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Reanuda una corrida existente a partir de su manifiesto guardado."""
    run_info = get_run(run_id, db_path=db_path)
    if run_info is None:
        raise ValueError(f'No se encontro la corrida {run_id}.')
    manifest = run_info['manifest']
    all_cases = {c['id']: c for c in cargar_banco_preguntas()}
    selected_cases = [all_cases[cid] for cid in manifest.get('case_ids', []) if cid in all_cases]
    quantizations = list((manifest.get('quantizations') or {}).keys())
    schemas = list(manifest.get('schemas') or [])
    return run_factorial_experiment(
        run_id=run_id,
        experiment_version=run_info['experiment_version'],
        mode=run_info['mode'],
        quantizations=quantizations,
        schemas=schemas,
        cases=selected_cases,
        repetitions=int(manifest.get('repetitions', 1)),
        seed=int(manifest.get('seed', 42)),
        db_user=db_user,
        db_password=db_password,
        shuffle=bool(manifest.get('shuffle', True)),
        resume_existing=True,
        on_progress=on_progress,
        should_stop=should_stop,
        db_path=db_path,
    )
