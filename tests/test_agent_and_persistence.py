"""Pruebas del evaluador, agente strict/assisted, runner y persistencia (T07-T12)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.agent import run_agent
from core.database import comparar_resultados
from core.experiment_runner import run_factorial_experiment
from core.metrics import build_factorial_summary, compute_kpis
from core.persistence import get_attempts, get_completed_keys, init_db


class _FakeModel:
    """Modelo simulado para probar el flujo del agente sin cargar GGUF."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0
        self.prompts_seen: list[str] = []

    def create_chat_completion(self, messages, **_kwargs):
        self.calls += 1
        self.prompts_seen.append('\n'.join(m['content'] for m in messages))
        text = self._responses.pop(0) if self._responses else ''
        return {
            'choices': [{'message': {'content': text}}],
            'usage': {'prompt_tokens': 50, 'completion_tokens': 12},
        }


class AgentAndPersistenceTests(unittest.TestCase):
    def test_t07_t08_t09_evaluator_semantics(self):
        # T07: orden distinto sin orden requerido -> acierta
        self.assertTrue(
            comparar_resultados(['a'], [(1,), (2,)], ['b'], [(2,), (1,)], orden=False)
        )
        # Orden requerido -> falla si cambia el orden
        self.assertFalse(
            comparar_resultados(['a'], [(1,), (2,)], ['b'], [(2,), (1,)], orden=True)
        )
        # T08: duplicados vs unico -> falla
        self.assertFalse(
            comparar_resultados(['a'], [(1,), (1,)], ['a'], [(1,)], orden=False)
        )
        # T09: cero filas vs cero filas -> acierta
        self.assertTrue(comparar_resultados(['a'], [], ['a'], [], orden=False))

    def test_t10_strict_mode_never_repairs_and_never_sees_gold(self):
        fake = _FakeModel(['DELETE FROM clientes', 'SELECT COUNT(*) FROM clientes'])
        cached_schema = ('CREATE TABLE clientes (id INT);', {'tablas': 1, 'columnas': 1, 'relaciones': 0}, {'clientes'})

        def _fake_exec(sql, *_args, **_kwargs):
            if sql == 'SELECT COUNT(*) FROM clientes':
                return ['count'], [(10,)], 0.002
            raise AssertionError(f'No deberia ejecutarse SQL invalido: {sql}')

        with patch('core.agent.ejecutar_sql_bd', side_effect=_fake_exec):
            res = run_agent(
                question='¿Cuántos clientes hay?',
                schema_id='baja',
                quantization_id='Q4_K_M',
                db_user='text_to_sql_lector',
                db_password='dummy',
                mode='strict',
                case_id='P01',
                gold_sql='SELECT COUNT(*) FROM clientes',
                model_override=fake,
                cached_schema=cached_schema,
            )

        self.assertEqual(fake.calls, 1)
        self.assertNotIn('REPAIRING', res.trace.states)
        self.assertEqual(res.trace.execution_accuracy, 0)
        self.assertIsNone(res.trace.repaired_sql)
        self.assertNotIn('SELECT COUNT(*) FROM clientes', fake.prompts_seen[0])

    def test_t11_assisted_mode_repairs_once_without_gold(self):
        fake = _FakeModel(['DELETE FROM clientes', 'SELECT COUNT(*) FROM clientes'])
        cached_schema = ('CREATE TABLE clientes (id INT);', {'tablas': 1, 'columnas': 1, 'relaciones': 0}, {'clientes'})

        def _fake_exec(sql, *_args, **_kwargs):
            return ['count'], [(10,)], 0.002

        with patch('core.agent.ejecutar_sql_bd', side_effect=_fake_exec):
            res = run_agent(
                question='¿Cuántos clientes hay?',
                schema_id='baja',
                quantization_id='Q4_K_M',
                db_user='text_to_sql_lector',
                db_password='dummy',
                mode='assisted',
                case_id='P01',
                gold_sql='SELECT COUNT(*) FROM clientes',
                model_override=fake,
                cached_schema=cached_schema,
            )

        self.assertEqual(fake.calls, 2)
        self.assertIn('REPAIRING', res.trace.states)
        self.assertEqual(res.trace.initial_sql, 'DELETE FROM clientes')
        self.assertEqual(res.trace.repaired_sql, 'SELECT COUNT(*) FROM clientes')
        self.assertEqual(res.trace.execution_accuracy, 1)
        # Ninguno de los prompts contiene la palabra gold ni resultados esperados
        for prompt in fake.prompts_seen:
            self.assertNotIn('10', prompt)

    def test_t12_runner_continues_on_exception_and_resumes_idempotently(self):
        cases = [
            {'id': 'P01', 'pregunta': 'Q1', 'orden': False, 'baja': 'SELECT 1', 'alta': 'SELECT 1'},
            {'id': 'P02', 'pregunta': 'Q2', 'orden': False, 'baja': 'SELECT 2', 'alta': 'SELECT 2'},
        ]
        call_counter = {'n': 0}

        def _fake_run_agent(**kwargs):
            call_counter['n'] += 1
            if kwargs['case_id'] == 'P01':
                raise RuntimeError('Fallo simulado en P01')
            from core.agent import AgentRunResult, AgentTrace
            import uuid
            trace = AgentTrace(
                attempt_id=str(uuid.uuid4()),
                run_id=kwargs['run_id'],
                case_id=kwargs['case_id'],
                question=kwargs['question'],
                mode=kwargs['mode'],
                quantization_id=kwargs['quantization_id'],
                schema_id=kwargs['schema_id'],
                repetition=kwargs['repetition'],
                initial_sql='SELECT 2',
                raw_output='SELECT 2',
                execution_accuracy=1,
                inference_ms=120.0,
                execution_ms=3.5,
                error_category='ok',
                status='correcta',
            )
            return AgentRunResult(trace=trace)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / 'test_exp.db'
            init_db(db_path)
            fake_schema = ('CREATE TABLE t (id INT);', {'tablas': 1, 'columnas': 1, 'relaciones': 0}, {'t': {}})
            with (
                patch('core.experiment_runner.obtener_tablas_y_esquema_bd', return_value=fake_schema),
                patch('core.experiment_runner.run_agent', side_effect=_fake_run_agent),
                patch('core.experiment_runner.listar_cuantizaciones_disponibles', return_value=[]),
            ):
                outcome = run_factorial_experiment(
                    run_id='run_test_t12',
                    experiment_version='v2.0',
                    mode='strict',
                    quantizations=['Q4_K_M'],
                    schemas=['baja'],
                    cases=cases,
                    repetitions=1,
                    seed=42,
                    db_user='text_to_sql_lector',
                    db_password='dummy',
                    shuffle=False,
                    db_path=db_path,
                )
                self.assertEqual(outcome['completed'], 2)
                self.assertEqual(outcome['status'], 'completed')

                # Reanudar la misma corrida no debe duplicar ni volver a invocar run_agent
                before = call_counter['n']
                outcome2 = run_factorial_experiment(
                    run_id='run_test_t12',
                    experiment_version='v2.0',
                    mode='strict',
                    quantizations=['Q4_K_M'],
                    schemas=['baja'],
                    cases=cases,
                    repetitions=1,
                    seed=42,
                    db_user='text_to_sql_lector',
                    db_password='dummy',
                    shuffle=False,
                    resume_existing=True,
                    db_path=db_path,
                )
                self.assertEqual(call_counter['n'], before)
                self.assertEqual(outcome2['completed'], 2)

            attempts = get_attempts(run_id='run_test_t12', db_path=db_path)
            self.assertEqual(len(attempts), 2)
            self.assertEqual(len(get_completed_keys('run_test_t12', db_path=db_path)), 2)

            kpis = compute_kpis(attempts)
            self.assertEqual(kpis['n_attempts'], 2)
            self.assertEqual(kpis['n_correct'], 1)
            self.assertAlmostEqual(kpis['accuracy_pct'], 50.0)

            summary = build_factorial_summary(attempts)
            self.assertEqual(len(summary), 1)
            self.assertEqual(int(summary.iloc[0]['N intentos']), 2)


if __name__ == '__main__':
    unittest.main()
