
from pathlib import Path
from llama_cpp import Llama

BASE_DIR = Path(__file__).resolve().parents[1]
MODELS_DIR = BASE_DIR / "models"

MODEL_PATH = (
    MODELS_DIR
    / "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
)

if not MODEL_PATH.is_file():
    raise FileNotFoundError(
        f"No se encontro el modelo: {MODEL_PATH}"
    )

print("Cargando modelo Qwen2.5-Coder...")

modelo = Llama(
    model_path=str(MODEL_PATH),
    n_ctx=2048,
    n_threads=4,
    n_gpu_layers=0,
    chat_format="chatml",
    verbose=False,
)

print("Modelo cargado correctamente.")

respuesta = modelo.create_chat_completion(
    messages=[
        {
            "role": "system",
            "content": (
                "Eres un generador de consultas SQL. "
                "Responde solo con SQL valido. "
                "No agregues explicaciones."
            ),
        },
        {
            "role": "user",
            "content": (
                "Base de datos PostgreSQL. "
                "Tabla estudiantes "
                "(id INTEGER, nombre TEXT, edad INTEGER). "
                "Genera una consulta que muestre "
                "los nombres de estudiantes "
                "mayores de 20 años."
            ),
        },
    ],
    temperature=0.0,
    max_tokens=150,
)

sql = respuesta["choices"][0]["message"]["content"]

print("\nSQL generado:")
print(sql)
