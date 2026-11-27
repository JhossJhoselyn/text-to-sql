"""Ejecución local serializada: una sola inferencia o lote a la vez."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from uuid import uuid4

logger = logging.getLogger(__name__)


class JobManager:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='sql-lab')
        self._lock = Lock()
        self._jobs = {}

    def submit(self, operation, kind):
        with self._lock:
            if any(j['status'] in ('queued', 'running') for j in self._jobs.values()):
                raise ValueError('Hay una tarea en curso. Espera a que termine o detenla.')
            while len(self._jobs) >= 30:
                self._jobs.pop(next(iter(self._jobs)))
            job_id = uuid4().hex
            self._jobs[job_id] = {'id': job_id, 'kind': kind, 'status': 'queued', 'progress': {}, 'cancel': False}
        self._executor.submit(self._execute, job_id, operation)
        return job_id

    def _execute(self, job_id, operation):
        self.update(job_id, status='running')
        try:
            result = operation(
                lambda progress: self.update(job_id, progress=progress),
                lambda: self.get(job_id).get('cancel', False),
            )
            self.update(job_id, status='completed', result=result)
        except Exception:
            logger.exception('Error en tarea %s', job_id)
            self.update(job_id, status='failed', error='No se pudo completar la tarea. Revisa la configuración de la base de datos y los modelos; el registro del servidor contiene el detalle técnico.')

    def update(self, job_id, **values):
        with self._lock:
            self._jobs[job_id].update(values)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def cancel(self, job_id):
        self.update(job_id, cancel=True)
