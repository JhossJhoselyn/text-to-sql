"""Agente Text-to-SQL de flujo controlado (modos estricto y asistido)."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import psutil
import psycopg

from core.database import (
    LimiteResultadosError,
    comparar_resultados,
    ejecutar_sql_bd,
    obtener_tablas_y_esquema_bd,
)
from core.model_adapter import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_N_CTX,
    DEFAULT_PROMPT_VERSION,
    DEFAULT_SEED,
    DEFAULT_TEMPERATURE,
    generar_sql_detallado,
    get_model_manager,
    reparar_sql_detallado,
)
from core.sql_validator import validate_sql

AgentMode = Literal['strict', 'assisted']


@dataclass
class AgentTrace:
    attempt_id: str
    run_id: str
    case_id: str
    question: str
    mode: AgentMode
    quantization_id: str
    schema_id: str
    repetition: int = 1
    prompt_version: str = DEFAULT_PROMPT_VERSION
    prompt_sha256: str = ''
    raw_output: str = ''
    initial_sql: str | None = None
    repaired_raw_output: str | None = None
    repaired_sql: str | None = None
    final_sql: str | None = None
    validation_error: str | None = None
    execution_error: str | None = None
    initial_error: str | None = None
    error_category: str = 'ok'
    execution_accuracy: int | None = None
    status: str = 'created'
    inference_ms: float | None = None
    repair_inference_ms: float | None = None
    execution_ms: float | None = None
    memory_peak_mb: float | None = None
    rows_returned: int | None = None
    gold_rows: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    states: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    timestamp_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def add_state(self, state: str, detail: str = '') -> None:
        self.states.append(state)
        self.events.append({
            'state': state,
            'detail': detail,
            'timestamp_utc': datetime.now(timezone.utc).isoformat(),
        })

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentRunResult:
    trace: AgentTrace
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    gold_columns: list[str] | None = None
    gold_rows_data: list[tuple] | None = None
    gold_sql: str | None = None
    schema_ddl: str = ''
    schema_metrics: dict[str, int] = field(default_factory=dict)


def _memory_rss_mb() -> float:
    try:
        rss = psutil.Process().memory_info().rss
        return round(rss / (1024 * 1024), 2)
    except Exception:
        return 0.0


def classify_pg_error(error: Exception) -> tuple[str, bool]:
    """Clasifica errores de ejecucion y determina si son atribuibles al SQL generado."""
    if isinstance(error, LimiteResultadosError):
        return 'timeout_or_limit', False
    if isinstance(error, psycopg.errors.QueryCanceled):
        return 'timeout_or_limit', False
    if isinstance(error, psycopg.OperationalError):
        return 'db_operational_error', False
    if isinstance(error, ValueError):
        return 'invalid_sql_policy', True

    code = getattr(error, 'sqlstate', '') or ''
    if code == '42601':
        return 'syntax_error', True
    if code in ('42P01', '42703', '42702', '42803', '42883'):
        return 'schema_error_table_or_column', True
    if code.startswith('22'):
        return 'type_or_data_error', True
    if code.startswith('42') or code.startswith('0A') or code == '25006':
        return 'syntax_error', True
    return 'db_operational_error', False


def _sanitize_error_for_repair(error: Exception) -> str:
    """Devuelve solo el mensaje tecnico relevante del error SQL sin rutas ni credenciales."""
    msg = str(error).strip()
    lines = [line.strip() for line in msg.splitlines() if line.strip()]
    return ' '.join(lines[:2])[:350]


def run_agent(
    question: str,
    schema_id: str,
    quantization_id: str,
    db_user: str,
    db_password: str,
    mode: AgentMode = 'strict',
    case_id: str = 'libre',
    run_id: str = 'demo',
    repetition: int = 1,
    gold_sql: str | None = None,
    require_order: bool = False,
    seed: int = DEFAULT_SEED,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    model_override=None,
    cached_schema: tuple[str, dict, set[str]] | None = None,
) -> AgentRunResult:
    """Ejecuta el agente acotado (strict = 1 pasada sin reparacion; assisted = max 1 reparacion)."""
    if mode not in ('strict', 'assisted'):
        raise ValueError(f'Modo de agente invalido: {mode}')

    trace = AgentTrace(
        attempt_id=str(uuid.uuid4()),
        run_id=run_id,
        case_id=case_id,
        question=question.strip(),
        mode=mode,
        quantization_id=quantization_id,
        schema_id=schema_id,
        repetition=repetition,
        prompt_version=prompt_version,
    )
    trace.add_state('CREATED', f'Modo={mode}, cuantizacion={quantization_id}, esquema={schema_id}')
    mem_peak = _memory_rss_mb()

    # 1. Inspeccionar esquema autorizado (sin mostrar gold al modelo).
    if cached_schema is not None:
        esquema, metricas, allowed_tables = cached_schema
    else:
        esquema, metricas, detalle_tablas = obtener_tablas_y_esquema_bd(
            schema_id, db_password, user=db_user
        )
        allowed_tables = set(detalle_tablas.keys())

    trace.add_state(
        'SCHEMA_LOADED',
        f"{metricas['tablas']} tablas, {metricas['columnas']} columnas, {metricas['relaciones']} FK",
    )

    # 2. Preparar referencia gold de forma independiente (NUNCA se pasa al modelo).
    gold_cols = None
    gold_rows_data = None
    if gold_sql:
        g_cols, g_rows, _ = ejecutar_sql_bd(
            gold_sql, schema_id, db_password, user=db_user, allowed_tables=allowed_tables
        )
        gold_cols = g_cols
        gold_rows_data = g_rows
        trace.gold_rows = len(g_rows)

    trace.add_state('PROMPT_READY', f'Plantilla {prompt_version} lista (sin referencia gold)')

    # 3. Generacion inicial (unica pasada en modo strict).
    try:
        if model_override is not None:
            gen = generar_sql_detallado(
                model_override,
                question,
                esquema,
                dialecto='PostgreSQL',
                prompt_version=prompt_version,
                temperature=temperature,
                max_tokens=max_tokens,
                seed=seed,
            )
        else:
            manager = get_model_manager()
            modelo = manager.get_model(cuantizacion=quantization_id, n_ctx=DEFAULT_N_CTX, seed=seed)
            with manager.lock:
                gen = generar_sql_detallado(
                    modelo,
                    question,
                    esquema,
                    dialecto='PostgreSQL',
                    prompt_version=prompt_version,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    seed=seed,
                )
    except Exception as error:
        trace.execution_error = _sanitize_error_for_repair(error)
        trace.error_category = 'inference_error'
        trace.status = 'error_inferencia'
        trace.execution_accuracy = 0 if gold_sql else None
        trace.memory_peak_mb = max(mem_peak, _memory_rss_mb())
        trace.add_state('FAILED', f'Error de inferencia: {trace.execution_error}')
        return AgentRunResult(
            trace=trace,
            gold_columns=gold_cols,
            gold_rows_data=gold_rows_data,
            gold_sql=gold_sql,
            schema_ddl=esquema,
            schema_metrics=metricas,
        )

    mem_peak = max(mem_peak, _memory_rss_mb())
    trace.raw_output = gen.raw_output
    trace.initial_sql = gen.extracted_sql
    trace.final_sql = gen.extracted_sql
    trace.prompt_sha256 = gen.prompt_sha256
    trace.inference_ms = round(gen.inference_ms, 2)
    trace.prompt_tokens = gen.prompt_tokens
    trace.completion_tokens = gen.completion_tokens
    trace.add_state('GENERATED', f'Inferencia completada en {trace.inference_ms:.1f} ms')

    # Helper para ejecutar validacion + PostgreSQL solo lectura.
    def _validate_and_execute(candidate_sql: str):
        verdict = validate_sql(candidate_sql, allowed_tables=allowed_tables)
        if not verdict.ok:
            return False, None, None, None, verdict.error, verdict.category, True, True
        try:
            cols, rows, exec_s = ejecutar_sql_bd(
                verdict.sql, schema_id, db_password, user=db_user, allowed_tables=allowed_tables
            )
            return True, cols, rows, exec_s * 1000.0, '', 'ok', False, False
        except Exception as err:
            cat, attributable = classify_pg_error(err)
            return False, None, None, None, _sanitize_error_for_repair(err), cat, False, attributable

    ok, cols, rows, exec_ms, err_msg, err_cat, is_val_err, attributable = _validate_and_execute(
        trace.initial_sql or ''
    )

    if ok:
        trace.add_state('VALIDATED', 'SQL valido segun politica de solo lectura')
        trace.add_state('EXECUTED', f'{len(rows)} filas en {exec_ms:.1f} ms')
    else:
        trace.initial_error = err_msg
        if is_val_err:
            trace.validation_error = err_msg
        else:
            trace.execution_error = err_msg
        trace.error_category = err_cat

        # Modo asistido: maximo UNA reparacion sin acceso al SQL gold.
        if mode == 'assisted' and attributable:
            trace.add_state('REPAIRING', f'Intentando 1 reparacion tras error: {err_cat}')
            try:
                if model_override is not None:
                    rep = reparar_sql_detallado(
                        model_override,
                        question,
                        esquema,
                        trace.initial_sql or '',
                        err_msg,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        seed=seed,
                    )
                else:
                    manager = get_model_manager()
                    modelo = manager.get_model(
                        cuantizacion=quantization_id, n_ctx=DEFAULT_N_CTX, seed=seed
                    )
                    with manager.lock:
                        rep = reparar_sql_detallado(
                            modelo,
                            question,
                            esquema,
                            trace.initial_sql or '',
                            err_msg,
                            temperature=temperature,
                            max_tokens=max_tokens,
                            seed=seed,
                        )
                mem_peak = max(mem_peak, _memory_rss_mb())
                trace.repaired_raw_output = rep.raw_output
                trace.repaired_sql = rep.extracted_sql
                trace.final_sql = rep.extracted_sql
                trace.repair_inference_ms = round(rep.inference_ms, 2)

                ok, cols, rows, exec_ms, err_msg2, err_cat2, is_val_err2, attributable2 = (
                    _validate_and_execute(trace.repaired_sql or '')
                )
                if ok:
                    trace.validation_error = None
                    trace.execution_error = None
                    trace.error_category = 'ok'
                    trace.add_state('VALIDATED', 'SQL reparado valido')
                    trace.add_state('EXECUTED', f'{len(rows)} filas en {exec_ms:.1f} ms (reparado)')
                else:
                    if is_val_err2:
                        trace.validation_error = err_msg2
                    else:
                        trace.execution_error = err_msg2
                    trace.error_category = err_cat2
                    attributable = attributable2
            except Exception as rep_err:
                trace.execution_error = _sanitize_error_for_repair(rep_err)
                trace.error_category = 'inference_error'

        if not ok:
            trace.execution_accuracy = 0 if (gold_sql and attributable) else None
            trace.status = 'error_validacion' if trace.validation_error else 'error_ejecucion'
            trace.memory_peak_mb = max(mem_peak, _memory_rss_mb())
            trace.add_state('FAILED', f'Fallo ({trace.error_category}): {trace.validation_error or trace.execution_error}')
            return AgentRunResult(
                trace=trace,
                gold_columns=gold_cols,
                gold_rows_data=gold_rows_data,
                gold_sql=gold_sql,
                schema_ddl=esquema,
                schema_metrics=metricas,
            )

    # 4. Evaluar contra referencia gold si existe.
    trace.execution_ms = round(exec_ms, 2) if exec_ms is not None else None
    trace.rows_returned = len(rows) if rows is not None else None
    if gold_cols is not None and gold_rows_data is not None:
        exact = int(comparar_resultados(cols, rows, gold_cols, gold_rows_data, orden=require_order))
        trace.execution_accuracy = exact
        if exact:
            trace.status = 'correcta'
            trace.error_category = 'ok'
            trace.add_state('EVALUATED', 'Exactitud = 1 (coincide con gold)')
        else:
            trace.status = 'incorrecta'
            trace.error_category = 'wrong_result'
            trace.add_state('EVALUATED', 'Exactitud = 0 (no coincide con gold)')
    else:
        trace.status = 'ejecutada_sin_referencia'
        trace.add_state('EVALUATED', 'Ejecutada sin consulta de referencia')

    trace.memory_peak_mb = max(mem_peak, _memory_rss_mb())
    trace.add_state('COMPLETED', f'Estado final: {trace.status}')

    return AgentRunResult(
        trace=trace,
        columns=cols or [],
        rows=rows or [],
        gold_columns=gold_cols,
        gold_rows_data=gold_rows_data,
        gold_sql=gold_sql,
        schema_ddl=esquema,
        schema_metrics=metricas,
    )
