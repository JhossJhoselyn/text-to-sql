"""Carga del modelo local GGUF, gestion de cuantizaciones y generacion SQL."""
from __future__ import annotations

import gc
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from llama_cpp import Llama

from core.sql_validator import extract_single_sql

BASE_DIR = Path(__file__).resolve().parents[1]
MODELS_DIR = (BASE_DIR / 'models').resolve()
PROMPTS_DIR = (BASE_DIR / 'config' / 'prompts').resolve()
RESULTS_DIR = (BASE_DIR / 'results').resolve()
HASH_CACHE_FILE = RESULTS_DIR / '.model_hashes.json'

MODEL_BASE_NAME = 'Qwen2.5-Coder-1.5B-Instruct'
DEFAULT_QUANTIZATION = 'Q4_K_M'
DEFAULT_N_CTX = 2048
DEFAULT_N_THREADS = 4
DEFAULT_MAX_TOKENS = 512
DEFAULT_TEMPERATURE = 0.0
DEFAULT_SEED = 42
DEFAULT_PROMPT_VERSION = 'v1'

QUANTIZATION_FILES = {
    'Q4_K_M': 'qwen2.5-coder-1.5b-instruct-q4_k_m.gguf',
    'Q8_0': 'qwen2.5-coder-1.5b-instruct-q8_0.gguf',
}

MODEL_PATH = MODELS_DIR / QUANTIZATION_FILES[DEFAULT_QUANTIZATION]


@dataclass(frozen=True)
class GenerationOutput:
    raw_output: str
    extracted_sql: str
    prompt_version: str
    prompt_sha256: str
    prompt_chars: int
    inference_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


def _resolve_within(base_dir: Path, target: Path) -> Path:
    resolved = target.resolve()
    if not resolved.is_relative_to(base_dir):
        raise ValueError('Ruta fuera del directorio autorizado.')
    return resolved


def obtener_ruta_modelo(cuantizacion: str = DEFAULT_QUANTIZATION) -> Path:
    if cuantizacion not in QUANTIZATION_FILES:
        disponibles = ', '.join(QUANTIZATION_FILES)
        raise ValueError(f'Cuantizacion no soportada: {cuantizacion}. Disponibles: {disponibles}')
    return _resolve_within(MODELS_DIR, MODELS_DIR / QUANTIZATION_FILES[cuantizacion])


def _leer_cache_hashes() -> dict:
    try:
        if HASH_CACHE_FILE.is_file():
            data = json.loads(HASH_CACHE_FILE.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def _guardar_cache_hashes(cache: dict) -> None:
    try:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        HASH_CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding='utf-8')
    except OSError:
        pass


def calcular_sha256_archivo(path: Path) -> str:
    """Calcula SHA-256 usando cache local por nombre, tamano y mtime."""
    resolved = path.resolve()
    stat = resolved.stat()
    cache_key = resolved.name
    cache = _leer_cache_hashes()
    entry = cache.get(cache_key)
    if (
        isinstance(entry, dict)
        and entry.get('size') == stat.st_size
        and entry.get('mtime_ns') == stat.st_mtime_ns
        and isinstance(entry.get('sha256'), str)
    ):
        return entry['sha256']

    digest = hashlib.sha256()
    with resolved.open('rb') as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            digest.update(chunk)
    sha_hex = digest.hexdigest()
    cache[cache_key] = {
        'size': stat.st_size,
        'mtime_ns': stat.st_mtime_ns,
        'sha256': sha_hex,
    }
    _guardar_cache_hashes(cache)
    return sha_hex


def listar_cuantizaciones_disponibles(calcular_hash: bool = False) -> list[dict]:
    """Devuelve metadatos verificables de las cuantizaciones GGUF locales."""
    items = []
    for quant_id, filename in QUANTIZATION_FILES.items():
        path = _resolve_within(MODELS_DIR, MODELS_DIR / filename)
        exists = path.is_file() and path.stat().st_size > 0
        size_bytes = path.stat().st_size if exists else 0
        sha256 = calcular_sha256_archivo(path) if (exists and calcular_hash) else None
        items.append({
            'quantization_id': quant_id,
            'model_base': MODEL_BASE_NAME,
            'filename': filename,
            'relative_path': f'models/{filename}',
            'exists': exists,
            'size_bytes': size_bytes,
            'size_gb': round(size_bytes / (1024 ** 3), 2) if exists else 0.0,
            'sha256': sha256,
        })
    return items


def cargar_prompt_plantilla(version: str = DEFAULT_PROMPT_VERSION) -> tuple[str, str, str]:
    """Carga la plantilla de prompt versionada y devuelve (version, texto, sha256)."""
    if version != 'v1':
        raise ValueError(f'Version de prompt no soportada: {version}')
    path = _resolve_within(PROMPTS_DIR, PROMPTS_DIR / f'text_to_sql_{version}.txt')
    if not path.is_file():
        raise FileNotFoundError(f'No se encontro la plantilla de prompt: {path.name}')
    texto = path.read_text(encoding='utf-8')
    sha256 = hashlib.sha256(texto.encode('utf-8')).hexdigest()
    return version, texto, sha256


def construir_prompt(pregunta: str, esquema: str, version: str = DEFAULT_PROMPT_VERSION) -> tuple[str, str, str]:
    """Construye el prompt final variando unicamente esquema y pregunta."""
    ver, plantilla, sha256 = cargar_prompt_plantilla(version)
    prompt = plantilla.format(schema_serializado=esquema, pregunta_es=pregunta.strip())
    return prompt, ver, sha256


def construir_prompt_reparacion(
    pregunta: str,
    esquema: str,
    sql_inicial: str,
    error_mensaje: str,
) -> str:
    """Construye prompt de reparacion (modo asistido) SIN incluir SQL gold ni respuestas esperadas."""
    return (
        'La consulta SQL anterior para PostgreSQL fallo al validarse o ejecutarse.\n'
        'Corrige la consulta usando EXCLUSIVAMENTE las tablas y columnas del esquema.\n'
        'Devuelve una sola sentencia SELECT (se permite WITH que termine en SELECT), sin explicaciones.\n\n'
        f'ESQUEMA:\n{esquema}\n\n'
        f'PREGUNTA:\n{pregunta.strip()}\n\n'
        f'SQL ANTERIOR:\n{sql_inicial}\n\n'
        f'ERROR OBSERVADO:\n{error_mensaje}\n\n'
        'SQL CORREGIDO:\n'
    )


def cargar_modelo(
    cuantizacion: str = DEFAULT_QUANTIZATION,
    n_ctx: int = DEFAULT_N_CTX,
    n_threads: int = DEFAULT_N_THREADS,
    n_gpu_layers: int = 0,
    seed: int = DEFAULT_SEED,
):
    """Carga una instancia llama.cpp de la cuantizacion especificada."""
    model_path = obtener_ruta_modelo(cuantizacion)
    if not model_path.is_file():
        raise FileNotFoundError(f'No existe el modelo GGUF para {cuantizacion}: {model_path.name}')
    return Llama(
        model_path=str(model_path),
        n_ctx=n_ctx,
        n_threads=n_threads,
        n_gpu_layers=n_gpu_layers,
        seed=seed,
        chat_format='chatml',
        verbose=False,
    )


def generar_sql_detallado(
    modelo,
    pregunta: str,
    esquema: str,
    dialecto: str = 'PostgreSQL',
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    seed: int = DEFAULT_SEED,
) -> GenerationOutput:
    """Genera SQL preservando la salida cruda literal y tiempos de inferencia."""
    if dialecto != 'PostgreSQL':
        raise ValueError('El unico dialecto disponible es PostgreSQL.')
    user_prompt, ver, sha256 = construir_prompt(pregunta, esquema, version=prompt_version)
    inicio = time.perf_counter()
    respuesta = modelo.create_chat_completion(
        messages=[
            {
                'role': 'system',
                'content': (
                    'Eres un traductor preciso de lenguaje natural en español a SQL para PostgreSQL. '
                    'Responde unicamente con una consulta SELECT valida usando el esquema provisto.'
                ),
            },
            {'role': 'user', 'content': user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
    )
    inference_ms = (time.perf_counter() - inicio) * 1000.0
    raw_output = respuesta['choices'][0]['message']['content'] or ''
    usage = respuesta.get('usage') or {}
    return GenerationOutput(
        raw_output=raw_output,
        extracted_sql=extract_single_sql(raw_output),
        prompt_version=ver,
        prompt_sha256=sha256,
        prompt_chars=len(user_prompt),
        inference_ms=inference_ms,
        prompt_tokens=usage.get('prompt_tokens'),
        completion_tokens=usage.get('completion_tokens'),
    )


def reparar_sql_detallado(
    modelo,
    pregunta: str,
    esquema: str,
    sql_inicial: str,
    error_mensaje: str,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    seed: int = DEFAULT_SEED,
) -> GenerationOutput:
    """Ejecuta una unica reparacion guiada por error, sin acceso al SQL gold."""
    repair_prompt = construir_prompt_reparacion(pregunta, esquema, sql_inicial, error_mensaje)
    inicio = time.perf_counter()
    respuesta = modelo.create_chat_completion(
        messages=[
            {
                'role': 'system',
                'content': (
                    'Corrige una consulta SQL de PostgreSQL con error. '
                    'Devuelve unicamente una sentencia SELECT valida, sin explicaciones.'
                ),
            },
            {'role': 'user', 'content': repair_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        seed=seed,
    )
    inference_ms = (time.perf_counter() - inicio) * 1000.0
    raw_output = respuesta['choices'][0]['message']['content'] or ''
    usage = respuesta.get('usage') or {}
    return GenerationOutput(
        raw_output=raw_output,
        extracted_sql=extract_single_sql(raw_output),
        prompt_version=f'{DEFAULT_PROMPT_VERSION}_repair',
        prompt_sha256=hashlib.sha256(repair_prompt.encode('utf-8')).hexdigest(),
        prompt_chars=len(repair_prompt),
        inference_ms=inference_ms,
        prompt_tokens=usage.get('prompt_tokens'),
        completion_tokens=usage.get('completion_tokens'),
    )


def generar_sql(modelo, pregunta: str, esquema: str, dialecto: str = 'PostgreSQL') -> str:
    """Interfaz compatible con versiones previas: devuelve el SQL extraido."""
    resultado = generar_sql_detallado(modelo, pregunta, esquema, dialecto=dialecto)
    return resultado.extracted_sql


class ModelManager:
    """Carga un solo modelo GGUF a la vez para evitar mezclar uso de memoria."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._active_quant: str | None = None
        self._model = None
        self._load_ms: float = 0.0

    @property
    def active_quantization(self) -> str | None:
        return self._active_quant

    @property
    def last_load_ms(self) -> float:
        return self._load_ms

    @property
    def lock(self) -> Lock:
        return self._lock

    def get_model(
        self,
        cuantizacion: str = DEFAULT_QUANTIZATION,
        n_ctx: int = DEFAULT_N_CTX,
        n_threads: int = DEFAULT_N_THREADS,
        seed: int = DEFAULT_SEED,
    ):
        with self._lock:
            if self._model is not None and self._active_quant == cuantizacion:
                return self._model
            if self._model is not None:
                self._model = None
                self._active_quant = None
                gc.collect()
            inicio = time.perf_counter()
            self._model = cargar_modelo(
                cuantizacion=cuantizacion,
                n_ctx=n_ctx,
                n_threads=n_threads,
                seed=seed,
            )
            self._load_ms = (time.perf_counter() - inicio) * 1000.0
            self._active_quant = cuantizacion
            return self._model


_GLOBAL_MANAGER = ModelManager()


def get_model_manager() -> ModelManager:
    return _GLOBAL_MANAGER
