-- Dataset ventas_v2. Aplicar únicamente en una base vacía.

CREATE TABLE clientes (
    id_cliente INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    correo TEXT UNIQUE,
    ciudad TEXT NOT NULL
);

CREATE TABLE productos (
    id_producto INTEGER PRIMARY KEY,
    nombre TEXT NOT NULL,
    categoria TEXT NOT NULL,
    precio NUMERIC(12,2) NOT NULL CHECK (precio > 0),
    activo BOOLEAN NOT NULL
);

CREATE TABLE ventas (
    id_venta INTEGER PRIMARY KEY,
    id_cliente INTEGER NOT NULL REFERENCES clientes,
    fecha DATE NOT NULL,
    estado TEXT NOT NULL CHECK (estado IN ('confirmada','cancelada')),
    sucursal TEXT NOT NULL,
    ciudad_sucursal TEXT NOT NULL,
    empleado TEXT NOT NULL
);

CREATE TABLE detalle_venta (
    id_detalle INTEGER PRIMARY KEY,
    id_venta INTEGER NOT NULL REFERENCES ventas,
    id_producto INTEGER NOT NULL REFERENCES productos,
    cantidad INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario NUMERIC(12,2) NOT NULL CHECK (precio_unitario > 0)
);

CREATE TABLE pagos (
    id_pago INTEGER PRIMARY KEY,
    id_venta INTEGER NOT NULL REFERENCES ventas,
    metodo TEXT NOT NULL,
    fecha DATE NOT NULL,
    monto NUMERIC(14,2) NOT NULL CHECK (monto > 0)
);

CREATE TABLE entregas (
    id_entrega INTEGER PRIMARY KEY,
    id_venta INTEGER NOT NULL UNIQUE REFERENCES ventas,
    fecha_prevista DATE NOT NULL,
    fecha_real DATE,
    CHECK (fecha_real IS NULL OR fecha_real >= fecha_prevista - 5)
);

CREATE TABLE devoluciones (
    id_devolucion INTEGER PRIMARY KEY,
    id_detalle INTEGER NOT NULL REFERENCES detalle_venta,
    fecha DATE NOT NULL,
    cantidad INTEGER NOT NULL CHECK (cantidad > 0),
    motivo TEXT NOT NULL
);

CREATE INDEX ON ventas (id_cliente);
CREATE INDEX ON detalle_venta (id_venta);
CREATE INDEX ON detalle_venta (id_producto);
CREATE INDEX ON pagos (id_venta);
CREATE INDEX ON devoluciones (id_detalle);
