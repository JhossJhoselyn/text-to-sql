"""Pruebas unitarias de seguridad y validacion SQL (política de consultas de solo lectura)."""
import unittest

from core.database import validar_nombre_usuario
from core.sql_validator import extract_single_sql, validate_sql


class SqlValidatorTests(unittest.TestCase):
    ALLOWED = frozenset({'clientes', 'ventas', 'productos', 'detalle_venta'})

    def test_t01_accepts_authorized_select(self):
        res = validate_sql('SELECT nombre FROM clientes;', allowed_tables=self.ALLOWED)
        self.assertTrue(res.ok)
        self.assertEqual(res.sql, 'SELECT nombre FROM clientes')
        self.assertEqual(res.referenced_tables, ('clientes',))

    def test_accepts_cte_with_select(self):
        sql = (
            'WITH resumen AS (SELECT id_cliente, COUNT(*) AS n FROM ventas GROUP BY id_cliente) '
            'SELECT c.nombre, r.n FROM clientes c JOIN resumen r ON r.id_cliente = c.id_cliente'
        )
        res = validate_sql(sql, allowed_tables=self.ALLOWED)
        self.assertTrue(res.ok)

    def test_t02_rejects_delete(self):
        res = validate_sql('DELETE FROM clientes', allowed_tables=self.ALLOWED)
        self.assertFalse(res.ok)
        self.assertEqual(res.category, 'invalid_sql_policy')

    def test_t03_rejects_multistatement(self):
        res = validate_sql('SELECT * FROM clientes; DROP TABLE clientes;', allowed_tables=self.ALLOWED)
        self.assertFalse(res.ok)
        self.assertIn('multiples sentencias', res.error.lower())

    def test_t04_rejects_copy(self):
        res = validate_sql("COPY clientes TO '/tmp/out.csv'", allowed_tables=self.ALLOWED)
        self.assertFalse(res.ok)

    def test_t06_rejects_write_in_cte_and_dangerous_functions(self):
        cte_write = 'WITH d AS (DELETE FROM clientes RETURNING *) SELECT * FROM d'
        self.assertFalse(validate_sql(cte_write, allowed_tables=self.ALLOWED).ok)

        fn_attack = "SELECT pg_read_file('/etc/passwd')"
        self.assertFalse(validate_sql(fn_attack, allowed_tables=self.ALLOWED).ok)

        pg_sleep = 'SELECT pg_sleep(10)'
        self.assertFalse(validate_sql(pg_sleep, allowed_tables=self.ALLOWED).ok)

        internal_schema = 'SELECT * FROM pg_catalog.pg_roles'
        self.assertFalse(validate_sql(internal_schema, allowed_tables=self.ALLOWED).ok)

    def test_rejects_unauthorized_table_and_select_into(self):
        res_unknown = validate_sql('SELECT * FROM usuarios_secretos', allowed_tables=self.ALLOWED)
        self.assertFalse(res_unknown.ok)
        self.assertEqual(res_unknown.category, 'schema_error_table_or_column')

        res_into = validate_sql('SELECT * INTO copia FROM clientes', allowed_tables=self.ALLOWED)
        self.assertFalse(res_into.ok)

    def test_extract_single_sql_from_markdown_fence(self):
        raw = 'Aqui tienes la consulta:\n```sql\nSELECT COUNT(*) FROM clientes;\n```'
        self.assertEqual(extract_single_sql(raw), 'SELECT COUNT(*) FROM clientes;')

    def test_validar_nombre_usuario(self):
        self.assertEqual(validar_nombre_usuario('text_to_sql_lector'), 'text_to_sql_lector')
        with self.assertRaises(ValueError):
            validar_nombre_usuario('admin; DROP TABLE x')
        with self.assertRaises(ValueError):
            validar_nombre_usuario('123invalid')


if __name__ == '__main__':
    unittest.main()
