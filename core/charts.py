"""Graficos interactivos Plotly para analisis experimental A x B."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


QUANTIZATION_COLORS = {'Q4_K_M': '#7dbb3e', 'Q8_0': '#2ea68b'}


def _apply_chart_style(fig: go.Figure) -> go.Figure:
    """Comparte la escala tipográfica entre todos los gráficos."""
    fig.update_layout(
        font=dict(family='Arial, sans-serif', size=14, color='#0F172A'),
        title_font_size=16,
        legend=dict(orientation='h', y=-0.3, x=0),
        margin=dict(t=60, b=100, l=55, r=35),
        xaxis_title_font_size=14,
        yaxis_title_font_size=14,
        legend_font_size=14,
        xaxis_tickfont_size=12,
        yaxis_tickfont_size=12,
    )
    return fig


def build_accuracy_bar_chart(summary_df: pd.DataFrame) -> go.Figure:
    """Grafico de barras de Execution Accuracy con IC 95% Wilson desagregado por complejidad."""
    fig = go.Figure()
    if summary_df.empty:
        fig.update_layout(title='Sin datos de exactitud disponibles')
        return _apply_chart_style(fig)

    for quant, grp in summary_df.groupby('Cuantización', sort=True):
        err_plus = (grp['IC 95% sup (%)'] - grp['Exactitud (%)']).clip(lower=0)
        err_minus = (grp['Exactitud (%)'] - grp['IC 95% inf (%)']).clip(lower=0)
        labels = [
            f"{acc:.1f}%"
            for acc, n in zip(grp['Exactitud (%)'], grp['N evaluados'])
        ]
        fig.add_trace(
            go.Bar(
                name=str(quant),
                x=grp['Complejidad'],
                y=grp['Exactitud (%)'],
                error_y={
                    'type': 'data',
                    'symmetric': False,
                    'array': err_plus.tolist(),
                    'arrayminus': err_minus.tolist(),
                },
                text=labels,
                textposition='outside',
                marker_color=QUANTIZATION_COLORS.get(str(quant)),
            )
        )

    fig.update_layout(
        title='Exactitud · IC 95% (Wilson)',
        xaxis_title='Complejidad',
        yaxis_title='Exactitud (%)',
        yaxis_range=[0, 115],
        barmode='group',
        legend_title='Cuantización',
        height=400,
        margin=dict(t=55, b=40, l=40, r=20),
    )
    return _apply_chart_style(fig)


def build_interaction_chart(summary_df: pd.DataFrame) -> go.Figure:
    """Grafico de interaccion A x B: eje X = complejidad, lineas = cuantizacion, Y = exactitud."""
    fig = go.Figure()
    if summary_df.empty:
        fig.update_layout(title='Sin datos para gráfico de interacción')
        return _apply_chart_style(fig)

    order = {'baja': 0, 'alta': 1}
    df_sorted = summary_df.copy()
    df_sorted['_ord'] = df_sorted['Complejidad'].map(lambda v: order.get(str(v), 99))
    df_sorted = df_sorted.sort_values('_ord')

    for quant, grp in df_sorted.groupby('Cuantización', sort=True):
        fig.add_trace(
            go.Scatter(
                name=str(quant),
                x=grp['Complejidad'],
                y=grp['Exactitud (%)'],
                mode='lines+markers+text',
                text=[f"{v:.1f}%" for v, n in zip(grp['Exactitud (%)'], grp['N evaluados'])],
                textposition='top center',
                line=dict(width=3, color=QUANTIZATION_COLORS.get(str(quant))),
                marker=dict(size=10),
            )
        )

    fig.update_layout(
        title='Interacción de los factores',
        xaxis_title='Complejidad',
        yaxis_title='Exactitud (%)',
        yaxis_range=[0, 120],
        legend_title='Cuantización',
        height=400,
        margin=dict(t=55, b=40, l=40, r=20),
    )
    return _apply_chart_style(fig)


def build_latency_chart(attempts_df: pd.DataFrame) -> go.Figure:
    """Boxplot interactivo de latencia de inferencia por cuantizacion y complejidad."""
    if attempts_df.empty or 'inference_ms' not in attempts_df.columns:
        fig = go.Figure()
        fig.update_layout(title='Sin datos de latencia')
        return _apply_chart_style(fig)

    clean_df = attempts_df.dropna(subset=['inference_ms']).copy()
    if clean_df.empty:
        fig = go.Figure()
        fig.update_layout(title='Sin mediciones de latencia')
        return _apply_chart_style(fig)

    fig = px.box(
        clean_df,
        x='schema_id',
        y='inference_ms',
        color='quantization_id',
        points='all',
        labels={
            'schema_id': 'Complejidad',
            'inference_ms': 'Latencia de inferencia (ms)',
            'quantization_id': 'Cuantización',
        },
        title='Latencia de inferencia (ms)',
        color_discrete_map=QUANTIZATION_COLORS,
    )
    fig.update_layout(height=380, margin=dict(t=55, b=40, l=40, r=20))
    return _apply_chart_style(fig)


def build_error_chart(error_df: pd.DataFrame) -> go.Figure:
    """Barras apiladas de categorias de resultado/error por condicion."""
    if error_df.empty:
        fig = go.Figure()
        fig.update_layout(title='Sin datos de errores')
        return _apply_chart_style(fig)

    df = error_df.copy()
    df['Condición'] = df['Cuantización'].astype(str) + ' · ' + df['Complejidad'].astype(str)
    fig = px.bar(
        df,
        x='Condición',
        y='Conteo',
        color='Categoría',
        color_discrete_map={'ok': '#7dbb3e'},
        color_discrete_sequence=['#ec9469', '#e4bb67', '#a89aeb', '#65b4e3', '#d98bb0'],
        text='Conteo',
        title='Resultados y errores por condición',
        labels={'Conteo': 'N intentos', 'Categoría': 'Categoría'},
    )
    fig.update_layout(barmode='stack', height=380, margin=dict(t=55, b=40, l=40, r=20))
    return _apply_chart_style(fig)
