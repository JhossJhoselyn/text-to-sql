"""Invariantes de negocio del dataset experimental, independientes del servidor."""
import unittest
from collections import defaultdict
from decimal import Decimal

from scripts.generar_datos import N_CLIENTES, N_PRODUCTOS, N_VENTAS, generar


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.datos = generar()
        cls.alta = dict(cls.datos['alta'])

    def test_reproducibilidad(self):
        self.assertEqual(self.datos, generar())

    def test_pagos_y_cancelaciones(self):
        totales = defaultdict(Decimal)
        cobros = defaultdict(Decimal)
        for _, venta, _, cantidad, precio in self.alta['detalle_venta']:
            totales[venta] += cantidad * precio
        for _, venta, _, _, monto in self.alta['pagos']:
            cobros[venta] += monto
        self.assertTrue(any(0 < cobros[v] < total for v, total in totales.items()))
        self.assertTrue(any(cobros[v] == 0 for v in totales))
        for venta, _, _, estado, _, _ in self.alta['ventas']:
            self.assertLessEqual(cobros[venta], totales[venta])
            if estado == 'cancelada':
                self.assertEqual(cobros[venta], 0)

    def test_devoluciones_y_casos_limite(self):
        detalles = {fila[0]: fila for fila in self.alta['detalle_venta']}
        devuelto = defaultdict(int)
        for _, detalle, _, cantidad, _ in self.alta['devoluciones']:
            devuelto[detalle] += cantidad
            self.assertLessEqual(devuelto[detalle], detalles[detalle][3])
        self.assertEqual(len(self.alta['clientes']), N_CLIENTES)
        self.assertEqual(len(self.alta['productos']), N_PRODUCTOS)
        self.assertEqual(len(self.alta['ventas']), N_VENTAS)
        clientes = {v[1] for v in self.alta['ventas']}
        productos = {d[2] for d in self.alta['detalle_venta']}
        self.assertLess(len(clientes), N_CLIENTES)
        self.assertLess(len(productos), N_PRODUCTOS)
        self.assertTrue(any(c[2] is None for c in self.alta['clientes']))
        self.assertTrue(any(e[3] is None for e in self.alta['entregas']))


if __name__ == '__main__':
    unittest.main()
