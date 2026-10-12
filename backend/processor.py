
from pathlib import Path
import subprocess
import sys

import imageio_ffmpeg

BASE_DIR = Path(__file__).resolve().parent.parent
PROCESSED_DIR = BASE_DIR / "clips" / "processed"

# 1.0 = sin zoom; 1.5 = 50% más grande
ZOOM = 1.5


def create_reel(input_path: Path) -> Path:
    input_path = input_path.resolve()

    if not input_path.is_file():
        raise FileNotFoundError(f"No existe el video: {input_path}")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    output_path = (
        PROCESSED_DIR / f"{input_path.stem}_reel.mp4"
    )

    # Fondo vertical desenfocado y gameplay ampliado.
    # El recorte central mantiene la mira en pantalla.



    video_filter = (
        "[0:v]"
        "scale=1080:1920:"
        "force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "setsar=1,"
        "format=yuv420p[video]"
    )





    command = [
        imageio_ffmpeg.get_ffmpeg_exe(),
        "-y",
        "-i", str(input_path),
        "-filter_complex", video_filter,
        "-map", "[video]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-af", "aresample=async=1:first_pts=0",
        "-movflags", "+faststart",
        str(output_path),
    ]

    print(f"Procesando: {input_path.name}")
    subprocess.run(command, check=True)

    print(f"Reel creado: {output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(
            "Uso: python -m backend.processor "
            "clips/highlights/video.mp4"
        )
        sys.exit(1)

    create_reel(Path(sys.argv[1]))
