"""Pruebas HTTP de la migración, con inferencia y PostgreSQL simulados."""
import json
import time
import unittest
from threading import Event
from unittest.mock import MagicMock, patch

import psycopg

from core.persistence import cargar_banco_preguntas

from core.agent import AgentRunResult, AgentTrace
from web import create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app({'TESTING': True, 'SECRET_KEY': 'test'})
        self.client = self.app.test_client()
        self.quants = patch('web.services.available_quants', return_value=['Q4_K_M', 'Q8_0'])
        self.quants.start()
        self.addCleanup(self.quants.stop)
        self.runs = patch('web.routes.list_runs', return_value=[])
        self.runs.start()
        self.addCleanup(self.runs.stop)
        self.client.get('/')
        with self.client.session_transaction() as session:
            self.headers = {'X-CSRF-Token': session['csrf_token']}
        self.addCleanup(lambda: self.app.extensions['jobs']._executor.shutdown(wait=True))

    def post(self, url, data):
        return self.client.post(url, json=data, headers=self.headers)

    def wait(self, job_id):
        for _ in range(100):
            result = self.client.get('/api/jobs/' + job_id).get_json()
            if result['status'] in ('completed', 'failed'):
                return result
            time.sleep(.01)
        self.fail('La tarea no terminó')

    def test_pages_and_local_assets(self):
        for url in ['/', '/experimentos', '/resultados', '/static/app.css', '/static/app.js', '/assets/plotly.min.js']:
            with self.client.get(url) as response:
                self.assertEqual(response.status_code, 200, url)
        html = self.client.get('/').get_data(as_text=True)
        self.assertNotIn('Cerrar sesión', html)
        self.assertNotIn('Auditoría', html)
        self.assertIn('<svg', html)

    def test_csrf_and_invalid_inputs(self):
        self.assertEqual(self.client.post('/api/demo', json={}).status_code, 403)
        self.assertEqual(self.post('/api/demo', {}).status_code, 400)
        self.assertEqual(self.post('/api/experiments', {}).status_code, 400)
        self.assertEqual(self.post('/api/demo', {'schema':'baja','quantization':'Q4_K_M','question':'   '}).status_code, 400)

    def test_demo_uses_env_credentials_and_persists_trace(self):
        result = AgentRunResult(trace=AgentTrace(
            attempt_id='a', run_id='demo_strict', case_id='P01', question='Pregunta', mode='strict',
            quantization_id='Q4_K_M', schema_id='baja', status='correcta', execution_accuracy=1,
        ), columns=['count'], rows=[(10,)])
        with (patch('web.services.credentials', return_value=('lector', 'secret')),
              patch('web.services.ensure_demo_run', return_value='demo_strict'),
              patch('web.services.next_demo_repetition', return_value=2),
              patch('web.services.record_attempt') as persist,
              patch('web.services.run_agent', return_value=result) as agent):
            response = self.post('/api/demo', {'schema':'baja','quantization':'Q4_K_M','case_id':'P01','save':True})
            self.assertEqual(response.status_code, 202)
            job = self.wait(response.json['id'])
            self.assertEqual(job['status'], 'completed')
            self.assertEqual(job['result']['rows'], [[10]])
            self.assertEqual(agent.call_args.kwargs['db_password'], 'secret')
            self.assertEqual(agent.call_args.kwargs['repetition'], 2)
            self.assertEqual(agent.call_args.kwargs['mode'], 'strict')
            persist.assert_called_once()
            self.assertNotIn('secret', json.dumps(job))

    def test_sql_error_preserves_expected_sql_and_rows(self):
        case = next(c for c in cargar_banco_preguntas() if 'cinco clientes con mayor importe pagado' in c['pregunta'])
        generated_sql = 'SELECT SUM(p.monto) FROM pagos p GROUP BY p.id_cliente'
        model = MagicMock()
        model.create_chat_completion.return_value = {
            'choices': [{'message': {'content': generated_sql}}],
            'usage': {},
        }
        manager = MagicMock()
        manager.get_model.return_value = model
        expected_rows = [(1, 2500), (2, 2000)]

        def execute(sql, *_args, **_kwargs):
            if sql == case['alta']:
                return ['id_cliente', 'total'], expected_rows, .001
            raise psycopg.errors.UndefinedColumn('column p.id_cliente does not exist')

        with (
            patch('web.services.credentials', return_value=('lector', 'secret')),
            patch('core.agent.get_model_manager', return_value=manager),
            patch('core.agent.obtener_tablas_y_esquema_bd', return_value=(
                'CREATE TABLE pagos (id_venta INTEGER, monto NUMERIC);',
                {'tablas': 1, 'columnas': 2, 'relaciones': 0}, {'pagos': {}},
            )),
            patch('core.agent.ejecutar_sql_bd', side_effect=execute),
        ):
            response = self.post('/api/demo', {
                'schema': 'alta', 'quantization': 'Q8_0', 'case_id': case['id'], 'save': False,
            })
            job = self.wait(response.json['id'])
            self.assertEqual(job['status'], 'completed')
            result = job['result']
            self.assertEqual(result['trace']['status'], 'error_ejecucion')
            self.assertIn('p.id_cliente', result['trace']['execution_error'])
            self.assertEqual(result['trace']['initial_sql'], generated_sql)
            self.assertEqual(result['rows'], [])
            self.assertEqual(result['gold_sql'], case['alta'])
            self.assertEqual(result['gold_rows_data'], [[1, 2500], [2, 2000]])

    def test_experiment_and_resume(self):
        outcome = {'run_id':'run_test','completed':2,'total':2,'status':'completed'}
        def execute(**kwargs):
            kwargs['on_progress']({'done':2, 'total':2})
            return outcome
        with (patch('web.routes.get_run', return_value=None),
              patch('web.services.credentials', return_value=('lector','secret')),
              patch('web.routes.run_factorial_experiment', side_effect=execute) as runner):
            response = self.post('/api/experiments', {'run_id':'run_test','quantizations':['Q4_K_M'], 'schemas':['baja'], 'cases':['P01'], 'repetitions':2,'seed':42})
            self.assertEqual(response.status_code,202)
            job=self.wait(response.json['id'])
            self.assertEqual(job['result'],outcome)
            self.assertEqual(job['progress']['done'],2)
            self.assertEqual(runner.call_args.kwargs['repetitions'],2)
        with (patch('web.routes.get_run', return_value={'mode':'strict'}),
              patch('web.services.credentials', return_value=('lector','secret')),
              patch('web.routes.resume_run_from_manifest', return_value=outcome) as resume):
            response=self.post('/api/resume/run_test',{})
            self.assertEqual(self.wait(response.json['id'])['status'],'completed')
            self.assertEqual(resume.call_args.args,('run_test','lector','secret'))

    def test_results_and_downloads_work_without_postgres(self):
        with patch('web.routes.get_attempts', return_value=[]), patch('web.routes.get_run', return_value=None):
            self.assertEqual(self.client.get('/api/results').json['kpis']['n_attempts'],0)
            for kind in ['csv','json','md']:
                response=self.client.get('/api/export/'+kind)
                self.assertEqual(response.status_code,200)
                self.assertIn('attachment',response.headers['Content-Disposition'])

    def test_job_failure_hides_internal_error(self):
        with patch('web.services.run_demo', side_effect=RuntimeError('secret-password')):
            response=self.post('/api/demo',{'schema':'baja','quantization':'Q4_K_M','question':'Pregunta'})
            job=self.wait(response.json['id'])
            self.assertEqual(job['status'],'failed')
            self.assertNotIn('secret-password',json.dumps(job))

    def test_stop_is_cooperative_and_rejects_concurrent_inference(self):
        manager=self.app.extensions['jobs']
        release=Event()
        def operation(progress, stopped):
            release.wait(2)
            return {'stopped':stopped()}
        job_id=manager.submit(operation,'experiment')
        try:
            with self.assertRaises(ValueError):
                manager.submit(operation,'demo')
            self.assertEqual(self.post('/api/jobs/'+job_id+'/stop',{}).status_code,200)
        finally:
            release.set()
        self.assertTrue(self.wait(job_id)['result']['stopped'])


if __name__ == '__main__':
    unittest.main()
