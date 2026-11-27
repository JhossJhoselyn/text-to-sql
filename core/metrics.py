"""Calculo de metricas experimentales, intervalos de confianza y resumenes."""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

import pandas as pd


def wilson_ci(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """Intervalo de confianza de Wilson al 95% para proporcion binomial."""
    if total <= 0:
        return 0.0, 0.0
    p = successes / total
    denom = 1.0 + (z * z) / total
    center = (p + (z * z) / (2.0 * total)) / denom
    half = (z / denom) * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * total)) / total)
    return max(0.0, center - half), min(1.0, center + half)


def compute_kpis(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula KPIs desde intentos persistidos sin excluir errores del denominador."""
    n_total = len(attempts)
    if n_total == 0:
        return {
            'n_attempts': 0,
            'n_evaluated': 0,
            'n_correct': 0,
            'accuracy_pct': None,
            'median_inference_ms': None,
            'median_execution_ms': None,
            'sql_errors': 0,
        }

    evaluated = [a for a in attempts if a.get('accuracy') is not None]
    n_eval = len(evaluated)
    n_correct = sum(1 for a in evaluated if int(a['accuracy']) == 1)
    acc_pct = (100.0 * n_correct / n_eval) if n_eval > 0 else None

    inf_vals = sorted(float(a['inference_ms']) for a in attempts if a.get('inference_ms') is not None)
    exe_vals = sorted(float(a['execution_ms']) for a in attempts if a.get('execution_ms') is not None)

    def _median(vals: list[float]) -> float | None:
        if not vals:
            return None
        mid = len(vals) // 2
        if len(vals) % 2 == 1:
            return vals[mid]
        return (vals[mid - 1] + vals[mid]) / 2.0

    sql_errors = sum(
        1 for a in attempts
        if a.get('error_category') not in (None, '', 'ok', 'wrong_result')
    )
    return {
        'n_attempts': n_total,
        'n_evaluated': n_eval,
        'n_correct': n_correct,
        'accuracy_pct': acc_pct,
        'median_inference_ms': _median(inf_vals),
        'median_execution_ms': _median(exe_vals),
        'sql_errors': sql_errors,
    }


def build_factorial_summary(attempts: list[dict[str, Any]]) -> pd.DataFrame:
    """Construye la tabla Cuantizacion x Complejidad con IC Wilson 95% y error principal."""
    columns = [
        'Cuantización', 'Complejidad', 'N intentos', 'N evaluados', 'N aciertos',
        'Exactitud (%)', 'IC 95% inf (%)', 'IC 95% sup (%)',
        'Latencia mediana (ms)', 'SQL mediana (ms)', 'Error principal',
    ]
    if not attempts:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(attempts)
    rows = []
    for (quant, schema), grp in df.groupby(['quantization_id', 'schema_id'], sort=True):
        n_total = len(grp)
        eval_grp = grp[grp['accuracy'].notna()]
        n_eval = len(eval_grp)
        n_correct = int((eval_grp['accuracy'] == 1).sum()) if n_eval > 0 else 0
        acc = (100.0 * n_correct / n_eval) if n_eval > 0 else 0.0
        low, high = wilson_ci(n_correct, n_eval)

        inf_series = grp['inference_ms'].dropna()
        exe_series = grp['execution_ms'].dropna()
        med_inf = float(inf_series.median()) if not inf_series.empty else None
        med_exe = float(exe_series.median()) if not exe_series.empty else None

        err_cats = [
            str(c) for c in grp['error_category'].dropna().tolist()
            if str(c) not in ('', 'ok')
        ]
        top_err = Counter(err_cats).most_common(1)[0][0] if err_cats else 'Ninguno'

        rows.append({
            'Cuantización': quant,
            'Complejidad': schema,
            'N intentos': n_total,
            'N evaluados': n_eval,
            'N aciertos': n_correct,
            'Exactitud (%)': round(acc, 2),
            'IC 95% inf (%)': round(low * 100.0, 2),
            'IC 95% sup (%)': round(high * 100.0, 2),
            'Latencia mediana (ms)': round(med_inf, 1) if med_inf is not None else None,
            'SQL mediana (ms)': round(med_exe, 2) if med_exe is not None else None,
            'Error principal': top_err,
        })

    return pd.DataFrame(rows, columns=columns)


def build_error_matrix(attempts: list[dict[str, Any]]) -> pd.DataFrame:
    """Desglose de categorias de error por cuantizacion y complejidad."""
    if not attempts:
        return pd.DataFrame(columns=['Cuantización', 'Complejidad', 'Categoría', 'Conteo', 'Porcentaje (%)'])

    df = pd.DataFrame(attempts)
    df['error_category'] = df['error_category'].fillna('desconocido')
    totals = df.groupby(['quantization_id', 'schema_id']).size().to_dict()
    grouped = (
        df.groupby(['quantization_id', 'schema_id', 'error_category'])
        .size()
        .reset_index(name='Conteo')
    )
    grouped['Porcentaje (%)'] = grouped.apply(
        lambda r: round(100.0 * r['Conteo'] / totals[(r['quantization_id'], r['schema_id'])], 2),
        axis=1,
    )
    grouped = grouped.rename(columns={
        'quantization_id': 'Cuantización',
        'schema_id': 'Complejidad',
        'error_category': 'Categoría',
    })
    return grouped.sort_values(['Cuantización', 'Complejidad', 'Conteo'], ascending=[True, True, False])


def generate_methodological_summary(
    run_info: dict[str, Any] | None,
    attempts: list[dict[str, Any]],
) -> str:
    """Genera un resumen metodologico reproducible en Markdown."""
    kpis = compute_kpis(attempts)
    summary_df = build_factorial_summary(attempts)
    mode = run_info['mode'] if run_info else 'multi_corrida'
    run_id = run_info['run_id'] if run_info else 'seleccion_filtrada'
    version = run_info['experiment_version'] if run_info else 'mixta'

    lines = [
        f'# Resumen Metodológico — {run_id}',
        '',
        f'- **Modo experimental:** `{mode}`',
        f'- **Versión del experimento:** `{version}`',
        f'- **N intentos totales:** {kpis["n_attempts"]}',
        f'- **N evaluados con referencia gold:** {kpis["n_evaluated"]}',
        (
            f'- **Execution Accuracy global:** {kpis["accuracy_pct"]:.2f}% '
            f'({kpis["n_correct"]} / {kpis["n_evaluated"]})'
            if kpis['accuracy_pct'] is not None
            else '- **Execution Accuracy global:** N/D'
        ),
        (
            f'- **Latencia mediana de inferencia:** {kpis["median_inference_ms"]:.1f} ms'
            if kpis['median_inference_ms'] is not None
            else '- **Latencia mediana de inferencia:** N/D'
        ),
        f'- **Errores SQL / validación:** {kpis["sql_errors"]}',
        '',
        '> **Nota metodológica:** Execution Accuracy = aciertos / total de intentos evaluados (sin excluir errores de sintaxis, validación ni ejecución). Solo las corridas en modo `strict` corresponden al análisis principal de hipótesis.',
        '',
        '## Tabla Cuantización × Complejidad',
        '',
    ]

    if summary_df.empty:
        lines.append('_Sin datos disponibles para los filtros seleccionados._')
    else:
        headers = list(summary_df.columns)
        lines.append('| ' + ' | '.join(headers) + ' |')
        lines.append('| ' + ' | '.join(['---'] * len(headers)) + ' |')
        for _, row in summary_df.iterrows():
            vals = [str(row[h]) if pd.notna(row[h]) else '—' for h in headers]
            lines.append('| ' + ' | '.join(vals) + ' |')

    return '\n'.join(lines) + '\n'
