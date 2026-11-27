"""Servidor local sin recargador para conservar la cola de inferencia."""
from argparse import ArgumentParser

from web import create_app

if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=5000, help='Puerto HTTP local (5000 por defecto).')
    args = parser.parse_args()
    create_app().run(host='127.0.0.1', port=args.port, threaded=True, use_reloader=False)
