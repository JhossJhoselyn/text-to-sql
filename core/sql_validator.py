"""Validador SQL estructural y de lista blanca para PostgreSQL (solo lectura)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# Palabras clave prohibidas en cualquier posicion fuera de literales/comentarios.
FORBIDDEN_KEYWORDS = frozenset({
    'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'UPSERT',
    'DROP', 'ALTER', 'CREATE', 'TRUNCATE', 'RENAME', 'COMMENT',
    'GRANT', 'REVOKE', 'SECURITY', 'OWNER',
    'COPY', 'CALL', 'DO', 'EXECUTE', 'PREPARE', 'DEALLOCATE',
    'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'RELEASE',
    'SET', 'RESET', 'SHOW', 'DISCARD', 'LISTEN', 'NOTIFY', 'UNLISTEN',
    'LOCK', 'VACUUM', 'ANALYZE', 'CLUSTER', 'REINDEX', 'REFRESH',
    'CHECKPOINT', 'LOAD', 'IMPORT', 'EXPLAIN',
})

# Funciones peligrosas de sistema/archivo/red/control en PostgreSQL.
FORBIDDEN_FUNCTIONS = frozenset({
    'pg_read_file', 'pg_read_binary_file', 'pg_ls_dir', 'pg_stat_file',
    'pg_sleep', 'pg_sleep_for', 'pg_sleep_until',
    'pg_terminate_backend', 'pg_cancel_backend', 'pg_reload_conf',
    'pg_rotate_logfile', 'pg_switch_wal', 'pg_create_restore_point',
    'lo_import', 'lo_export', 'lo_create', 'lo_unlink', 'lo_open',
    'lo_write', 'lo_put', 'lo_from_bytea',
    'dblink', 'dblink_exec', 'dblink_connect', 'dblink_open',
    'set_config', 'current_setting', 'query_to_xml', 'cursor_to_xml',
    'txid_current', 'pg_advisory_lock', 'pg_advisory_xact_lock',
})

# Esquemas internos prohibidos para consultas experimentales.
FORBIDDEN_SCHEMAS = frozenset({
    'pg_catalog', 'information_schema', 'pg_toast', 'pg_temp',
})

_TOKEN_RE = re.compile(
    r"""
    (?P<dollar_quote>\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$)
    | (?P<block_comment>/\*[\s\S]*?\*/)
    | (?P<line_comment>--[^\n]*)
    | (?P<single_quote>'(?:''|[^'])*')
    | (?P<double_quote>"(?:""|[^"])*")
    | (?P<semicolon>;)
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<dot>\.)
    | (?P<word>[A-Za-z_][A-Za-z0-9_$]*)
    | (?P<other>\S)
    """,
    re.VERBOSE,
)

_CODE_FENCE_RE = re.compile(r'```(?:sql)?\s*([\s\S]*?)\s*```', re.IGNORECASE)


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    sql: str
    error: str = ''
    category: str = 'ok'
    referenced_tables: tuple[str, ...] = field(default_factory=tuple)


def extract_single_sql(raw_output: str) -> str:
    """Extrae la consulta SQL candidata sin alterar la salida cruda original."""
    if not raw_output:
        return ''
    text = raw_output.strip()
    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()
    else:
        text = re.sub(r'^```(?:sql)?\s*|\s*```$', '', text, flags=re.IGNORECASE).strip()

    # Eliminar prefijos frecuentes del modelo como "SQL:" o "Consulta SQL:"
    text = re.sub(r'^(?:consulta\s+)?sql\s*:\s*', '', text, flags=re.IGNORECASE).strip()
    return text


def _tokenize_sql(sql_text: str) -> list[tuple[str, str]]:
    """Tokeniza SQL detectando comentarios, cadenas, identificadores y simbolos."""
    tokens: list[tuple[str, str]] = []
    pos = 0
    length = len(sql_text)
    while pos < length:
        if sql_text[pos].isspace():
            pos += 1
            continue
        match = _TOKEN_RE.match(sql_text, pos)
        if not match:
            pos += 1
            continue
        kind = match.lastgroup or 'other'
        value = match.group(0)
        if kind == 'dollar_quote':
            end = sql_text.find(value, match.end())
            if end == -1:
                raise ValueError('Literal con delimitador $ sin cerrar.')
            tokens.append(('string', sql_text[pos:end + len(value)]))
            pos = end + len(value)
            continue
        if kind in ('block_comment', 'line_comment'):
            pos = match.end()
            continue
        if kind == 'single_quote':
            tokens.append(('string', value))
        elif kind == 'double_quote':
            tokens.append(('ident', value[1:-1].replace('""', '"')))
        elif kind == 'word':
            tokens.append(('word', value))
        else:
            tokens.append((kind, value))
        pos = match.end()

    # Verificar comentarios o comillas sin cerrar
    if '/*' in sql_text and '*/' not in sql_text:
        raise ValueError('Comentario de bloque SQL sin cerrar.')
    return tokens


def validate_sql(
    sql_text: str,
    allowed_tables: set[str] | frozenset[str] | None = None,
) -> ValidationResult:
    """Valida que el texto sea una unica consulta SELECT/WITH de solo lectura."""
    cleaned = (sql_text or '').strip()
    if not cleaned:
        return ValidationResult(
            ok=False,
            sql='',
            error='La consulta SQL esta vacia.',
            category='invalid_sql_policy',
        )

    if len(cleaned) > 10_000:
        return ValidationResult(
            ok=False,
            sql=cleaned,
            error='La consulta excede la longitud maxima permitida (10 000 caracteres).',
            category='invalid_sql_policy',
        )

    try:
        tokens = _tokenize_sql(cleaned)
    except ValueError as err:
        return ValidationResult(
            ok=False,
            sql=cleaned,
            error=str(err),
            category='syntax_error',
        )

    if not tokens:
        return ValidationResult(
            ok=False,
            sql=cleaned,
            error='No se encontro ninguna instruccion SQL valida.',
            category='invalid_sql_policy',
        )

    # Permitir un unico punto y coma final; rechazar multisentencia.
    while tokens and tokens[-1][0] == 'semicolon':
        tokens.pop()
    if any(kind == 'semicolon' for kind, _ in tokens):
        return ValidationResult(
            ok=False,
            sql=cleaned,
            error='Se rechazan multiples sentencias SQL (punto y coma intermedio).',
            category='invalid_sql_policy',
        )

    # Normalizar quitando punto y coma final para el ejecutor preparado.
    normalized_sql = cleaned.rstrip().rstrip(';').strip()

    first_kind, first_val = tokens[0]
    first_upper = first_val.upper()
    if first_kind != 'word' or first_upper not in ('SELECT', 'WITH'):
        return ValidationResult(
            ok=False,
            sql=normalized_sql,
            error='Solo se permiten consultas de lectura que inicien con SELECT o WITH.',
            category='invalid_sql_policy',
        )

    # Balanceo de parentesis + escaneo de palabras prohibidas y funciones peligrosas.
    depth = 0
    has_top_level_select = first_upper == 'SELECT'
    cte_names: set[str] = set()
    referenced_tables: list[str] = []

    for idx, (kind, value) in enumerate(tokens):
        if kind == 'lparen':
            depth += 1
            continue
        if kind == 'rparen':
            depth -= 1
            if depth < 0:
                return ValidationResult(
                    ok=False,
                    sql=normalized_sql,
                    error='Parentesis desbalanceados en la consulta SQL.',
                    category='syntax_error',
                )
            continue

        upper = value.upper()
        lower = value.lower()

        if kind == 'word':
            if upper in FORBIDDEN_KEYWORDS:
                return ValidationResult(
                    ok=False,
                    sql=normalized_sql,
                    error=f'Operacion SQL no permitida por politica de solo lectura: {upper}.',
                    category='invalid_sql_policy',
                )

            # SELECT ... INTO crea tablas en PostgreSQL -> prohibido.
            if upper == 'INTO':
                return ValidationResult(
                    ok=False,
                    sql=normalized_sql,
                    error='La clausula INTO no esta permitida en consultas de lectura.',
                    category='invalid_sql_policy',
                )

            # FOR UPDATE / FOR SHARE / FOR NO KEY UPDATE -> bloqueo de filas prohibido.
            if upper == 'FOR' and idx + 1 < len(tokens):
                nxt = tokens[idx + 1][1].upper()
                if nxt in ('UPDATE', 'SHARE', 'NO', 'KEY'):
                    return ValidationResult(
                        ok=False,
                        sql=normalized_sql,
                        error='Las clausulas de bloqueo de filas (FOR UPDATE/SHARE) estan prohibidas.',
                        category='invalid_sql_policy',
                    )

            if depth == 0 and upper == 'SELECT':
                has_top_level_select = True

            # Detectar llamadas a funciones peligrosas: nombre( ... )
            if idx + 1 < len(tokens) and tokens[idx + 1][0] == 'lparen':
                if lower in FORBIDDEN_FUNCTIONS or lower.startswith('pg_'):
                    return ValidationResult(
                        ok=False,
                        sql=normalized_sql,
                        error=f'Funcion de sistema no permitida: {value}.',
                        category='invalid_sql_policy',
                    )

            # Detectar acceso a esquemas internos (pg_catalog.xxx, information_schema.xxx)
            if idx + 1 < len(tokens) and tokens[idx + 1][0] == 'dot':
                if lower in FORBIDDEN_SCHEMAS:
                    return ValidationResult(
                        ok=False,
                        sql=normalized_sql,
                        error=f'Acceso al esquema interno no permitido: {value}.',
                        category='invalid_sql_policy',
                    )

            # Registrar nombres de CTE definidos en WITH cte_name AS (...)
            if upper == 'AS' and idx > 0 and idx + 1 < len(tokens) and tokens[idx + 1][0] == 'lparen':
                prev_kind, prev_val = tokens[idx - 1]
                if prev_kind in ('word', 'ident'):
                    cte_names.add(prev_val.lower())

            # Recolectar tablas referenciadas tras FROM o JOIN (cuando no es subconsulta)
            if upper in ('FROM', 'JOIN') and idx + 1 < len(tokens):
                next_kind, next_val = tokens[idx + 1]
                if next_kind in ('word', 'ident'):
                    table_name = next_val.lower()
                    # Soportar calificacion public.tabla
                    if (
                        idx + 3 < len(tokens)
                        and tokens[idx + 2][0] == 'dot'
                        and tokens[idx + 3][0] in ('word', 'ident')
                    ):
                        schema_name = table_name
                        if schema_name != 'public':
                            return ValidationResult(
                                ok=False,
                                sql=normalized_sql,
                                error=f'Esquema no autorizado: {next_val}. Solo se permite public.',
                                category='invalid_sql_policy',
                            )
                        table_name = tokens[idx + 3][1].lower()
                    if table_name not in ('lateral', 'only', 'unnest', 'generate_series'):
                        referenced_tables.append(table_name)

    if depth != 0:
        return ValidationResult(
            ok=False,
            sql=normalized_sql,
            error='Parentesis sin cerrar en la consulta SQL.',
            category='syntax_error',
        )

    if not has_top_level_select:
        return ValidationResult(
            ok=False,
            sql=normalized_sql,
            error='Las consultas WITH deben finalizar con una sentencia SELECT principal.',
            category='invalid_sql_policy',
        )

    # Validar tablas contra la lista autorizada del esquema si se proporciono.
    if allowed_tables is not None:
        allowed_lower = {t.lower() for t in allowed_tables}
        unknown = [
            t for t in referenced_tables
            if t not in allowed_lower and t not in cte_names
        ]
        if unknown:
            uniq_unknown = sorted(set(unknown))
            return ValidationResult(
                ok=False,
                sql=normalized_sql,
                error=f"Tablas fuera del esquema autorizado: {', '.join(uniq_unknown)}.",
                category='schema_error_table_or_column',
                referenced_tables=tuple(referenced_tables),
            )

    return ValidationResult(
        ok=True,
        sql=normalized_sql,
        error='',
        category='ok',
        referenced_tables=tuple(dict.fromkeys(referenced_tables)),
    )
