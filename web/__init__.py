"""Aplicación Flask del laboratorio Text-to-SQL."""
from __future__ import annotations

import os
import secrets

from flask import Flask, jsonify, request, session

from web.jobs import JobManager


def create_app(test_config=None):
    from web.routes import bp

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv('FLASK_SECRET_KEY') or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=64 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Strict',
    )
    if test_config:
        app.config.update(test_config)
    app.extensions['jobs'] = JobManager()

    @app.before_request
    def protect_writes():
        if 'csrf_token' not in session:
            session['csrf_token'] = secrets.token_hex(32)
        if request.method == 'POST':
            token = request.headers.get('X-CSRF-Token', '')
            if not secrets.compare_digest(token, session['csrf_token']):
                return jsonify(error='Recarga la página antes de continuar.'), 403

    app.register_blueprint(bp)

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(error='La solicitud supera el tamaño permitido.'), 413

    @app.errorhandler(500)
    def internal_error(_error):
        if request.path.startswith('/api/'):
            return jsonify(error='No se pudo completar la solicitud. Revisa el registro del servidor.'), 500
        from flask import render_template
        return render_template('error.html', title='No se pudo cargar la página'), 500

    return app
