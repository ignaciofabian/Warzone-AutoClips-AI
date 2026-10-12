
import json
import subprocess
import sys
from pathlib import Path
import imageio_ffmpeg

RAW_DIR = Path("clips/raw")
OUTPUT_DIR = Path("clips/highlights")
HIGHLIGHTS_DIR = Path("data/highlights")


def generar_clips(nombre_video):
    ruta_video = RAW_DIR / Path(nombre_video).name
    nombre_base = ruta_video.stem
    ruta_json = HIGHLIGHTS_DIR / f"{nombre_base}.json"

    if not ruta_video.exists():
        print(f"No existe el video: {ruta_video}")
        return

    if not ruta_json.exists():
        print(f"No existe el archivo de highlights: {ruta_json}")
        return

    with ruta_json.open("r", encoding="utf-8") as archivo:
        highlights = json.load(archivo)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for indice, highlight in enumerate(highlights, start=1):
        inicio = float(highlight["inicio_clip"])
        fin = float(highlight["fin_clip"])
        duracion = fin - inicio

        if duracion <= 0:
            print(f"Highlight {indice}: duración inválida.")
            continue

        salida = OUTPUT_DIR / f"{nombre_base}_highlight_{indice}.mp4"

        comando = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-ss", str(inicio),
            "-i", str(ruta_video),
            "-t", str(duracion),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-c:a", "aac",
            "-movflags", "+faststart",
            str(salida),
        ]

        print(
            f"Generando highlight {indice}: "
            f"{inicio:.1f}s - {fin:.1f}s"
        )

        resultado = subprocess.run(comando, check=False)

        if resultado.returncode == 0:
            print(f"Clip generado: {salida}")
        else:
            print(f"Error al generar highlight {indice}.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            'Uso: python -m backend.clip_generator '
            '"Replay 2026-10-11 22-48-41.mkv"'
        )
        sys.exit(1)

    generar_clips(sys.argv[1])
