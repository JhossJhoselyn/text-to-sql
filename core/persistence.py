"""Persistencia reproducible de corridas e intentos experimentales (SQLite aislado)."""
from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.agent import AgentTrace

BASE_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = (BASE_DIR / 'results').resolve()
DEFAULT_DB_PATH = RESULTS_DIR / 'experiments.db'
QUESTIONS_PATH = (BASE_DIR / 'datasets' / 'preguntas_postgresql.json').resolve()
DATASET_META_PATH = (BASE_DIR / 'results' / 'dataset_ventas_v2.json').resolve()


def _connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    resolved = db_path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(resolved), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Crea las tablas SQLite de corridas e intentos experimentales."""
    with _connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS experiment_runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                experiment_version TEXT NOT NULL,
                mode TEXT NOT NULL,
                manifest_json TEXT NOT NULL,
                status TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                case_id TEXT NOT NULL,
                quantization_id TEXT NOT NULL,
                schema_id TEXT NOT NULL,
                repetition INTEGER NOT NULL,
                raw_output TEXT,
                initial_sql TEXT,
                repaired_sql TEXT,
                validation_error TEXT,
                execution_error TEXT,
                accuracy INTEGER CHECK (accuracy IN (0, 1) OR accuracy IS NULL),
                inference_ms REAL,
                execution_ms REAL,
                memory_peak_mb REAL,
                error_category TEXT,
                trace_json TEXT NOT NULL,
                FOREIGN KEY(run_id) REFERENCES experiment_runs(run_id),
                UNIQUE(run_id, case_id, quantization_id, schema_id, repetition)
            );
        """)


def create_run(
    run_id: str,
    experiment_version: str,
    mode: str,
    manifest: dict[str, Any],
    status: str = 'running',
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    """Registra el manifiesto inmutable antes de iniciar la ejecucion."""
    init_db(db_path)
    created_at = datetime.now(timezone.utc).isoformat()
    manifest_str = json.dumps(manifest, ensure_ascii=False, indent=2)
    with _connect(db_path) as conn:
        conn.execute(
            'INSERT INTO experiment_runs (run_id, created_at, experiment_version, mode, manifest_json, status) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (run_id, created_at, experiment_version, mode, manifest_str, status),
        )
    return {
        'run_id': run_id,
        'created_at': created_at,
        'experiment_version': experiment_version,
        'mode': mode,
        'manifest': manifest,
        'status': status,
    }


def update_run_status(run_id: str, status: str, db_path: Path = DEFAULT_DB_PATH) -> None:
    init_db(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            'UPDATE experiment_runs SET status = ? WHERE run_id = ?',
            (status, run_id),
        )


def record_attempt(trace: AgentTrace, db_path: Path = DEFAULT_DB_PATH) -> bool:
    """Inserta un intento sin sobrescribir claves existentes (reanudacion idempotente)."""
    init_db(db_path)
    trace_str = json.dumps(trace.to_dict(), ensure_ascii=False)
    with _connect(db_path) as conn:
        cur = conn.execute(
            'INSERT OR IGNORE INTO attempts ('
            'attempt_id, run_id, case_id, quantization_id, schema_id, repetition, '
            'raw_output, initial_sql, repaired_sql, validation_error, execution_error, '
            'accuracy, inference_ms, execution_ms, memory_peak_mb, error_category, trace_json'
            ') VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (
                trace.attempt_id,
                trace.run_id,
                trace.case_id,
                trace.quantization_id,
                trace.schema_id,
                trace.repetition,
                trace.raw_output,
                trace.initial_sql,
                trace.repaired_sql,
                trace.validation_error,
                trace.execution_error,
                trace.execution_accuracy,
                trace.inference_ms,
                trace.execution_ms,
                trace.memory_peak_mb,
                trace.error_category,
                trace_str,
            ),
        )
        return cur.rowcount > 0


def ensure_demo_run(mode: str, manifest: dict[str, Any], db_path: Path = DEFAULT_DB_PATH) -> str:
    """Asegura un contenedor separado para intentos guardados desde la vista Demo."""
    init_db(db_path)
    run_id = f'demo_{mode}'
    with _connect(db_path) as conn:
        row = conn.execute(
            'SELECT run_id FROM experiment_runs WHERE run_id = ?',
            (run_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                'INSERT INTO experiment_runs (run_id, created_at, experiment_version, mode, manifest_json, status) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (
                    run_id,
                    datetime.now(timezone.utc).isoformat(),
                    'demo_exploratorio',
                    f'demo_{mode}',
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                    'exploratory',
                ),
            )
    return run_id


def next_demo_repetition(
    run_id: str,
    case_id: str,
    quantization_id: str,
    schema_id: str,
    db_path: Path = DEFAULT_DB_PATH,
) -> int:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            'SELECT COALESCE(MAX(repetition), 0) AS max_rep FROM attempts '
            'WHERE run_id = ? AND case_id = ? AND quantization_id = ? AND schema_id = ?',
            (run_id, case_id, quantization_id, schema_id),
        ).fetchone()
    return int(row['max_rep']) + 1


def get_completed_keys(run_id: str, db_path: Path = DEFAULT_DB_PATH) -> set[tuple[str, str, str, int]]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            'SELECT case_id, quantization_id, schema_id, repetition FROM attempts WHERE run_id = ?',
            (run_id,),
        ).fetchall()
    return {
        (row['case_id'], row['quantization_id'], row['schema_id'], int(row['repetition']))
        for row in rows
    }


def list_runs(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    init_db(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            'SELECT r.run_id, r.created_at, r.experiment_version, r.mode, r.manifest_json, r.status, '
            'COUNT(a.attempt_id) AS attempts_count, '
            'SUM(CASE WHEN a.accuracy = 1 THEN 1 ELSE 0 END) AS correct_count '
            'FROM experiment_runs r '
            'LEFT JOIN attempts a ON a.run_id = r.run_id '
            'GROUP BY r.run_id '
            'ORDER BY r.created_at DESC'
        ).fetchall()
    result = []
    for row in rows:
        try:
            manifest = json.loads(row['manifest_json'])
        except ValueError:
            manifest = {}
        result.append({
            'run_id': row['run_id'],
            'created_at': row['created_at'],
            'experiment_version': row['experiment_version'],
            'mode': row['mode'],
            'status': row['status'],
            'manifest': manifest,
            'manifest_json': row['manifest_json'],
            'attempts_count': int(row['attempts_count'] or 0),
            'correct_count': int(row['correct_count'] or 0),
        })
    return result


def get_run(run_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any] | None:
    init_db(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            'SELECT run_id, created_at, experiment_version, mode, manifest_json, status '
            'FROM experiment_runs WHERE run_id = ?',
            (run_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        manifest = json.loads(row['manifest_json'])
    except ValueError:
        manifest = {}
    return {
        'run_id': row['run_id'],
        'created_at': row['created_at'],
        'experiment_version': row['experiment_version'],
        'mode': row['mode'],
        'status': row['status'],
        'manifest': manifest,
        'manifest_json': row['manifest_json'],
    }


def get_attempts(
    run_id: str | None = None,
    mode: str | None = None,
    quantization_id: str | None = None,
    schema_id: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> list[dict[str, Any]]:
    """Lee intentos persistidos con filtros seguros parametrizados."""
    init_db(db_path)
    clauses: list[str] = []
    params: list[Any] = []
    if run_id:
        clauses.append('a.run_id = ?')
        params.append(run_id)
    if mode:
        clauses.append('r.mode = ?')
        params.append(mode)
    if quantization_id:
        clauses.append('a.quantization_id = ?')
        params.append(quantization_id)
    if schema_id:
        clauses.append('a.schema_id = ?')
        params.append(schema_id)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ''
    query = (
        'SELECT a.*, r.mode AS run_mode, r.experiment_version, r.created_at AS run_created_at '
        'FROM attempts a '
        'JOIN experiment_runs r ON r.run_id = a.run_id '
        f'{where_sql} '
        'ORDER BY a.rowid DESC'
    )
    with _connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def export_attempts_csv(attempts: list[dict[str, Any]]) -> str:
    """Exporta intentos persistidos a CSV reproducible."""
    fieldnames = [
        'attempt_id', 'run_id', 'run_mode', 'experiment_version', 'case_id',
        'quantization_id', 'schema_id', 'repetition', 'accuracy',
        'inference_ms', 'execution_ms', 'memory_peak_mb', 'error_category',
        'validation_error', 'execution_error', 'initial_sql', 'repaired_sql',
        'raw_output',
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    for row in attempts:
        writer.writerow(row)
    return buffer.getvalue()


def cargar_banco_preguntas() -> list[dict[str, Any]]:
    if not QUESTIONS_PATH.is_file():
        raise FileNotFoundError('No se encontro el archivo datasets/preguntas_postgresql.json.')
    return json.loads(QUESTIONS_PATH.read_text(encoding='utf-8'))


def cargar_metadatos_dataset() -> dict[str, Any]:
    if not DATASET_META_PATH.is_file():
        return {}
    try:
        return json.loads(DATASET_META_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
