
from pathlib import Path
import subprocess
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "clips" / "raw"
PROCESSED_DIR = BASE_DIR / "clips" / "processed"


def create_reel(input_path: Path) -> Path:
    if not input_path.is_file():
        raise FileNotFoundError(f"No existe el video: {input_path}")

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    output_path = PROCESSED_DIR / f"{input_path.stem}_reel.mp4"

    video_filter = (
        "[0:v]split=2[background][gameplay];"
        "[background]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=30:10[blurred];"
        "[gameplay]scale=1080:1920:force_original_aspect_ratio=decrease"
        "[center];"
        "[blurred][center]overlay=(W-w)/2:(H-h)/2,"
        "format=yuv420p[video]"
    )

    command = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),
        "-filter_complex", video_filter,
        "-map", "[video]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    print(f"Procesando: {input_path.name}")

    subprocess.run(command, check=True)

    print(f"Reel creado: {output_path}")
    return output_path


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Uso: python -m backend.processor clips/raw/video.mp4")
        sys.exit(1)

    create_reel(Path(sys.argv[1]).resolve())
