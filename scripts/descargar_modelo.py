"""Descarga los archivos GGUF requeridos por el laboratorio."""
from argparse import ArgumentParser
from pathlib import Path
from huggingface_hub import hf_hub_download

# Carpeta donde se guardará el modelo
BASE_DIR = Path(__file__).resolve().parents[1]
MODELS_DIR = BASE_DIR / "models"

# Modelos GGUF de la misma familia y del mismo modelo base para el experimento.
REPO_ID = "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"
MODELOS = {
    "Q4_K_M": "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
    "Q8_0": "qwen2.5-coder-1.5b-instruct-q8_0.gguf",
}


def descargar_modelo(cuantizacion="Q8_0"):
    """Descarga una variante de cuantización del modelo Qwen 1.5B Instruct."""
    if cuantizacion not in MODELOS:
        raise ValueError(f"Cuantizaciones disponibles: {', '.join(MODELOS)}")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODELS_DIR / MODELOS[cuantizacion]
    if model_path.is_file() and model_path.stat().st_size > 0:
        print("Modelo encontrado:")
        print(model_path)
        print("Tamaño:", round(
            model_path.stat().st_size / (1024 ** 3), 2
        ), "GB")
        return model_path

    print("Descargando modelo Qwen2.5-Coder-1.5B...")
    print(f"Cuantización: {cuantizacion}")

    try:
        ruta = hf_hub_download(
            repo_id=REPO_ID,
            filename=MODELOS[cuantizacion],
            local_dir=str(MODELS_DIR)
        )

        ruta = Path(ruta)

        print("Modelo descargado correctamente.")
        print("Ubicación:", ruta)

        return ruta

    except Exception as error:
        print("Error al descargar el modelo:")
        print(error)
        raise


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('cuantizaciones', nargs='*', metavar='CUANTIZACIÓN',
                        help='Q4_K_M o Q8_0. Sin argumentos se descargan ambas.')
    args = parser.parse_args()
    for cuantizacion in args.cuantizaciones or MODELOS:
        if cuantizacion not in MODELOS:
            parser.error(f"Cuantización desconocida: {cuantizacion}. Usa {', '.join(MODELOS)}.")
    for cuantizacion in args.cuantizaciones or MODELOS:
        descargar_modelo(cuantizacion)


if __name__ == "__main__":
    main()

