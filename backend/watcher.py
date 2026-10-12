
from pathlib import Path
from datetime import datetime, timezone
import json
import time

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "clips" / "raw"
DATA_DIR = BASE_DIR / "data"
DATABASE_FILE = DATA_DIR / "clips.json"

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm"}
SCAN_INTERVAL = 5


def load_clips():
    if not DATABASE_FILE.exists():
        return {}

    with DATABASE_FILE.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_clips(clips):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    temporary_file = DATABASE_FILE.with_suffix(".tmp")

    with temporary_file.open("w", encoding="utf-8") as file:
        json.dump(clips, file, indent=4, ensure_ascii=False)

    temporary_file.replace(DATABASE_FILE)


def scan_clips():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    clips = load_clips()

    new_clips = []

    for video in RAW_DIR.iterdir():
        if not video.is_file():
            continue

        if video.suffix.lower() not in VIDEO_EXTENSIONS:
            continue

        if video.name in clips:
            continue

        # Esperamos a que el archivo deje de modificarse
        # para evitar registrar una grabación incompleta.
        initial_stat = video.stat()
        time.sleep(2)

        if not video.exists():
            continue

        final_stat = video.stat()

        if (
            initial_stat.st_size != final_stat.st_size
            or initial_stat.st_mtime_ns != final_stat.st_mtime_ns
        ):
            continue

        clips[video.name] = {
            "filename": video.name,
            "status": "pending_analysis",
            "size_bytes": final_stat.st_size,
            "detected_at": datetime.now(timezone.utc).isoformat()
        }

        new_clips.append(video.name)
        print(f"Nuevo clip detectado: {video.name}", flush=True)

    if new_clips:
        save_clips(clips)

    return new_clips


def start_watcher():
    print("Warzone AutoClips AI - Detector iniciado")
    print(f"Vigilando: {RAW_DIR}")
    print("Presiona CTRL+C para detenerlo.")

    try:
        while True:
            scan_clips()
            time.sleep(SCAN_INTERVAL)
    except KeyboardInterrupt:
        print("\nDetector detenido.")


if __name__ == "__main__":
    start_watcher()
