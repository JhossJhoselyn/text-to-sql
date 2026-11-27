"""Páginas, tareas y exportaciones del laboratorio."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from flask import Blueprint, Response, abort, current_app, jsonify, render_template, request, send_file
from plotly import __file__ as plotly_package_path
from plotly.utils import PlotlyJSONEncoder

from core.charts import build_accuracy_bar_chart, build_error_chart, build_interaction_chart, build_latency_chart
from core.experiment_runner import resume_run_from_manifest, run_factorial_experiment
from core.metrics import build_error_matrix, build_factorial_summary, compute_kpis, generate_methodological_summary
from core.persistence import cargar_banco_preguntas, export_attempts_csv, get_attempts, get_run, list_runs
from web import services

bp = Blueprint('lab', __name__)


@bp.get('/assets/plotly.min.js')
def plotly_asset():
    # Sirve la versión incluida en la dependencia Python para mantener ambos lados sincronizados.
    path = Path(plotly_package_path).parent / 'package_data' / 'plotly.min.js'
    return send_file(path, mimetype='text/javascript')


def json_response(value):
    return Response(json.dumps(value, cls=PlotlyJSONEncoder, ensure_ascii=False), mimetype='application/json')


def payload():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError('La solicitud debe contener un objeto JSON.')
    return value


def integer(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'Introduce un número entero entre {minimum} y {maximum}.')
    return value


@bp.errorhandler(ValueError)
def invalid(error):
    return jsonify(error=str(error)), 400


@bp.app_errorhandler(404)
def missing(_error):
    if request.path.startswith('/api/'):
        return jsonify(error='No se encontró el recurso solicitado.'), 404
    return render_template('error.html', title='Página no encontrada'), 404


@bp.route('/')
def demo():
    return render_template('demo.html', title='Demostración', active='demo', quants=services.available_quants(), cases=cargar_banco_preguntas())


@bp.route('/experimentos')
def experiments():
    return render_template('experiments.html', title='Experimentos', active='experiments', quants=services.available_quants(), cases=cargar_banco_preguntas(), runs=list_runs(), default_run=f"run_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_strict")


@bp.route('/resultados')
def results():
    return render_template('results.html', title='Resultados', active='results', runs=list_runs())


@bp.post('/api/demo')
def submit_demo():
    data = payload()
    services.validate_selection(data)
    if data.get('case_id'):
        if not isinstance(data['case_id'], str) or data['case_id'] not in {c['id'] for c in cargar_banco_preguntas()}:
            raise ValueError('La pregunta seleccionada no existe.')
    elif not isinstance(data.get('question'), str) or not 1 <= len(data['question'].strip()) <= 2000:
        raise ValueError('Escribe una pregunta de hasta 2000 caracteres.')
    if not isinstance(data.get('save', True), bool):
        raise ValueError('La opción de guardado no es válida.')
    job_id = current_app.extensions['jobs'].submit(lambda _progress, _stop: services.run_demo(data), 'demo')
    return jsonify(id=job_id), 202


@bp.get('/api/schema/<schema>')
def schema(schema):
    try:
        return jsonify(services.load_schema(schema))
    except ValueError:
        return jsonify(error='Revisa el esquema y las credenciales en infra/.env.'), 400
    except Exception:
        current_app.logger.exception('Error al consultar el esquema')
        return jsonify(error='PostgreSQL no está disponible o el rol no tiene acceso de lectura.'), 503


@bp.post('/api/experiments')
def submit_experiment():
    data = payload()
    quants, schemas = data.get('quantizations'), data.get('schemas')
    ids = data.get('cases')
    if not isinstance(quants, list) or not quants or any(not isinstance(q, str) or q not in services.available_quants() for q in quants):
        raise ValueError('Selecciona al menos una cuantización disponible.')
    if not isinstance(schemas, list) or not schemas or any(not isinstance(s, str) or s not in ('baja', 'alta') for s in schemas):
        raise ValueError('Selecciona al menos un esquema válido.')
    cases = cargar_banco_preguntas()
    if not isinstance(ids, list) or not ids or any(not isinstance(cid, str) or cid not in {c['id'] for c in cases} for cid in ids):
        raise ValueError('Selecciona preguntas válidas para el lote.')
    run_id = data.get('run_id', '')
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
        raise ValueError('El identificador admite hasta 80 letras, números, guiones y guiones bajos.')
    if get_run(run_id):
        raise ValueError('Este identificador ya existe. Usa otro o reanuda la corrida guardada.')
    reps, seed = integer(data.get('repetitions'), 1, 5), integer(data.get('seed'), 0, 999999)
    version = data.get('version', 'v2.0')
    if not isinstance(version, str) or not 1 <= len(version) <= 40:
        raise ValueError('La versión debe tener entre 1 y 40 caracteres.')
    def execute(progress, stop):
        user, password = services.credentials()
        return run_factorial_experiment(
            run_id=run_id, experiment_version=version, mode='strict',
            quantizations=list(dict.fromkeys(quants)), schemas=list(dict.fromkeys(schemas)),
            cases=[c for c in cases if c['id'] in ids], repetitions=reps, seed=seed,
            db_user=user, db_password=password, shuffle=data.get('shuffle', True) is True,
            on_progress=progress, should_stop=stop,
        )
    return jsonify(id=current_app.extensions['jobs'].submit(execute, 'experiment')), 202


@bp.post('/api/resume/<run_id>')
def resume(run_id):
    run = get_run(run_id)
    if not run or run['mode'] not in ('strict', 'assisted'):
        raise ValueError('La corrida no puede reanudarse.')
    def execute(progress, stop):
        user, password = services.credentials()
        return resume_run_from_manifest(run_id, user, password, on_progress=progress, should_stop=stop)
    return jsonify(id=current_app.extensions['jobs'].submit(execute, 'experiment')), 202


@bp.get('/api/jobs/<job_id>')
def job(job_id):
    value = current_app.extensions['jobs'].get(job_id)
    if value is None:
        abort(404)
    return json_response(value)


@bp.post('/api/jobs/<job_id>/stop')
def stop_job(job_id):
    jobs = current_app.extensions['jobs']
    job = jobs.get(job_id)
    if not job:
        abort(404)
    if job['kind'] != 'experiment':
        raise ValueError('Solo los lotes pueden detenerse entre intentos.')
    jobs.cancel(job_id)
    return jsonify(message='Se detendrá al finalizar el intento actual.')


def filtered_attempts():
    return get_attempts(run_id=request.args.get('run') or None, mode=request.args.get('mode') or None,
                        quantization_id=request.args.get('quant') or None, schema_id=request.args.get('schema') or None)


@bp.get('/api/results')
def result_data():
    attempts = filtered_attempts()
    summary, errors = build_factorial_summary(attempts), build_error_matrix(attempts)
    figures = [build_accuracy_bar_chart(summary), build_interaction_chart(summary),
               build_latency_chart(pd.DataFrame(attempts)), build_error_chart(errors)]
    return json_response({'kpis': compute_kpis(attempts), 'summary': json.loads(summary.to_json(orient='records')),
                          'errors': json.loads(errors.to_json(orient='records')),
                          'attempts': attempts, 'charts': [f.to_plotly_json() for f in figures]})


@bp.get('/api/export/<kind>')
def export(kind):
    attempts = filtered_attempts()
    run = get_run(request.args['run']) if request.args.get('run') else None
    if kind == 'csv':
        content, mime, filename = export_attempts_csv(attempts), 'text/csv', 'intentos.csv'
    elif kind == 'json':
        content = json.dumps(run['manifest'] if run else [r['manifest'] for r in list_runs() if r['run_id'] in {a['run_id'] for a in attempts}], ensure_ascii=False, indent=2)
        mime, filename = 'application/json', 'manifiesto.json'
    elif kind == 'md':
        content, mime, filename = generate_methodological_summary(run, attempts), 'text/markdown', 'resumen.md'
    else:
        abort(404)
    return Response(content, mimetype=mime, headers={'Content-Disposition': f'attachment; filename="{filename}"'})
