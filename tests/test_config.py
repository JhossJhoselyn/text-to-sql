"""Comprueba las credenciales de PostgreSQL obtenidas del entorno."""
import os
import unittest
from unittest.mock import patch

from config.database import obtener_credenciales_lector


class EnvironmentCredentialsTests(unittest.TestCase):
    def test_reads_reader_credentials_and_preserves_password(self):
        with patch.dict(os.environ, {
            'TEXT_TO_SQL_READER_USER': ' lector ',
            'TEXT_TO_SQL_READER_PASSWORD': ' password-with-spaces ',
        }, clear=True):
            self.assertEqual(obtener_credenciales_lector(), ('lector', ' password-with-spaces '))

    def test_missing_reader_password_does_not_use_admin_password(self):
        with patch.dict(os.environ, {'POSTGRES_ADMIN_PASSWORD': 'admin-secret'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'TEXT_TO_SQL_READER_PASSWORD'):
                obtener_credenciales_lector()


if __name__ == '__main__':
    unittest.main()
