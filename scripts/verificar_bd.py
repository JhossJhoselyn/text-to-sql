"""Verifica esquema y consultas gold con el usuario lector."""
from config.database import obtener_credenciales_lector
from core.database import verificar_acceso_bd
from scripts.preparar_bd import validar_gold
from scripts.validar_datos import validar_datos


def main():
    user, password = obtener_credenciales_lector()
    summary = verificar_acceso_bd(user, password)
    for base in summary['bases'].values():
        print(base['dbname'], base['metricas'])
    validar_datos(password, user=user)
    validar_gold(password, user=user)


if __name__ == '__main__':
    main()
