"""Dataset común ventas_v2: dos representaciones de los mismos hechos."""
import random
from datetime import date, timedelta
from decimal import Decimal
from getpass import getpass

from faker import Faker
from psycopg import sql

from config.database import conectar_bd

VERSION = 'ventas_v2'
SEMILLA = 42
N_CLIENTES = 3000
N_PRODUCTOS = 1200
N_VENTAS = 25000
CIUDADES = ['La Paz', 'El Alto', 'Cochabamba', 'Santa Cruz', 'Oruro',
            'Sucre', 'Potosí', 'Tarija', 'Trinidad', 'Cobija']
CATEGORIAS = ['Computación', 'Telefonía', 'Audio', 'Video', 'Oficina', 'Hogar',
              'Deportes', 'Ropa', 'Libros', 'Juguetes', 'Jardín', 'Papelería']
METODOS = ['Efectivo', 'Tarjeta', 'Transferencia', 'QR']
COLUMNAS = {
    'baja': {
        'clientes': 'id_cliente,nombre,correo,ciudad',
        'productos': 'id_producto,nombre,categoria,precio,activo',
        'ventas': 'id_venta,id_cliente,fecha,estado,sucursal,ciudad_sucursal,empleado',
        'detalle_venta': 'id_detalle,id_venta,id_producto,cantidad,precio_unitario',
        'pagos': 'id_pago,id_venta,metodo,fecha,monto',
        'entregas': 'id_entrega,id_venta,fecha_prevista,fecha_real',
        'devoluciones': 'id_devolucion,id_detalle,fecha,cantidad,motivo',
    },
    'alta': {
        'ciudades': 'id_ciudad,nombre', 'categorias': 'id_categoria,nombre',
        'sucursales': 'id_sucursal,nombre,id_ciudad',
        'empleados': 'id_empleado,id_sucursal,nombre',
        'clientes': 'id_cliente,nombre,correo,id_ciudad',
        'productos': 'id_producto,nombre,id_categoria,precio,activo',
        'ventas': 'id_venta,id_cliente,fecha,estado,id_sucursal,id_empleado',
        'detalle_venta': 'id_detalle,id_venta,id_producto,cantidad,precio_unitario',
        'metodos_pago': 'id_metodo,nombre',
        'pagos': 'id_pago,id_venta,id_metodo,fecha,monto',
        'entregas': 'id_entrega,id_venta,fecha_prevista,fecha_real',
        'devoluciones': 'id_devolucion,id_detalle,fecha,cantidad,motivo',
    },
}


def generar():
    rng = random.Random(SEMILLA)
    fake = Faker('es_ES')
    fake.seed_instance(SEMILLA)
    alta = {tabla: [] for tabla in COLUMNAS['alta']}
    alta['ciudades'] = list(enumerate(CIUDADES, 1))
    alta['categorias'] = list(enumerate(CATEGORIAS, 1))
    alta['metodos_pago'] = list(enumerate(METODOS, 1))
    alta['sucursales'] = [(i, f'Sucursal {ciudad}', i) for i, ciudad in enumerate(CIUDADES, 1)]
    alta['empleados'] = [(i, (i-1) % 10+1, f'{fake.name()} ({i})') for i in range(1, 61)]
    alta['clientes'] = [(i, fake.name(), None if i % 13 == 0 else f'cliente{i}@example.com',
                         (i-1) % 10+1) for i in range(1, N_CLIENTES+1)]
    # Precios repetidos y categorías con varios productos permiten probar empates.
    alta['productos'] = [(i, f'Producto {i:04}', (i-1) % len(CATEGORIAS)+1,
                          Decimal((i % 100+1)*25), i % 11 != 0)
                         for i in range(1, N_PRODUCTOS+1)]
    for i in range(1, N_VENTAS+1):
        fecha = date(2024, 1, 1) + timedelta(days=rng.randrange(731))
        cancelada = i % 17 == 0
        empleado = alta['empleados'][rng.randrange(60)]
        # Reservar clientes y productos sin operaciones.
        alta['ventas'].append((i, rng.randint(1, N_CLIENTES-100), fecha,
                               'cancelada' if cancelada else 'confirmada', empleado[1], empleado[0]))
        total = Decimal(0)
        lineas = []
        for producto in rng.sample(alta['productos'][:-60], rng.randint(1, 5)):
            cantidad = rng.randint(1, 5)
            precio = producto[3]
            detalle = len(alta['detalle_venta'])+1
            alta['detalle_venta'].append((detalle, i, producto[0], cantidad, precio))
            lineas.append((detalle, cantidad))
            total += cantidad * precio
        if cancelada:
            continue
        # Sin pagos, pago parcial, pago único y dos pagos, sin exceder total.
        if i % 7:
            monto = total/2 if i % 5 == 0 else total
            partes = [monto/2, monto/2] if i % 3 == 0 else [monto]
            for parte in partes:
                alta['pagos'].append((len(alta['pagos'])+1, i, rng.randint(1, 4),
                                      fecha + timedelta(days=rng.randrange(4)), parte))
        prevista = fecha + timedelta(days=5)
        real = None if i % 9 == 0 else prevista + timedelta(days=(i % 5)-1)
        alta['entregas'].append((len(alta['entregas'])+1, i, prevista, real))
        if real is not None and i % 10 == 0:
            detalle, cantidad = lineas[0]
            alta['devoluciones'].append((len(alta['devoluciones'])+1, detalle,
                                         real+timedelta(days=2), max(1, cantidad-1),
                                         'Defecto' if i % 20 == 0 else 'Cambio'))
    baja = {tabla: alta[tabla] for tabla in COLUMNAS['baja']}
    baja['clientes'] = [(i, n, correo, CIUDADES[ciudad-1]) for i, n, correo, ciudad in alta['clientes']]
    baja['productos'] = [(i, n, CATEGORIAS[c-1], p, a) for i, n, c, p, a in alta['productos']]
    baja['ventas'] = [(i, c, f, e, alta['sucursales'][s-1][1], CIUDADES[s-1],
                        alta['empleados'][em-1][2]) for i, c, f, e, s, em in alta['ventas']]
    baja['pagos'] = [(i, v, METODOS[m-1], f, monto) for i, v, m, f, monto in alta['pagos']]
    return {'baja': list(baja.items()), 'alta': list(alta.items())}


def cargar_datos(password):
    datos = generar()
    with conectar_bd('baja', password) as baja, conectar_bd('alta', password) as alta:
        conexiones = {'baja': baja, 'alta': alta}
        for nivel, conn in conexiones.items():
            with conn.cursor() as cur:
                for tabla, _ in datos[nivel]:
                    identificador = sql.Identifier('public', tabla)
                    cur.execute(sql.SQL('LOCK TABLE {} IN SHARE ROW EXCLUSIVE MODE').format(identificador))
                    cur.execute(sql.SQL('SELECT EXISTS (SELECT 1 FROM {})').format(identificador))
                    if cur.fetchone()[0]:
                        raise RuntimeError(f'{nivel}.{tabla} contiene datos; no se modifica.')
        for nivel, conn in conexiones.items():
            with conn.cursor() as cur:
                for tabla, filas in datos[nivel]:
                    columnas = COLUMNAS[nivel][tabla].split(',')
                    consulta = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
                        sql.Identifier('public', tabla),
                        sql.SQL(',').join(map(sql.Identifier, columnas)),
                        sql.SQL(',').join(sql.Placeholder() for _ in columnas))
                    cur.executemany(consulta, filas)
                    print(f'{nivel}.{tabla}: {len(filas):,} registros')
    print('Carga finalizada en ambas bases.')


if __name__ == '__main__':
    cargar_datos(getpass('Contraseña administradora de PostgreSQL: '))
