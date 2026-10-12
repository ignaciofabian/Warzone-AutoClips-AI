
from pathlib import Path
import cv2

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "clips" / "raw"
FRAMES_DIR = BASE_DIR / "data" / "frames"


def extract_frames(video_path: Path, interval_seconds: int = 2):
    if not video_path.is_file():
        raise FileNotFoundError(f"No existe el video: {video_path}")

    if interval_seconds <= 0:
        raise ValueError("El intervalo debe ser mayor que cero")

    FRAMES_DIR.mkdir(parents=True, exist_ok=True)

    capture = cv2.VideoCapture(str(video_path))

    if not capture.isOpened():
        raise RuntimeError("No se pudo abrir el video")

    fps = capture.get(cv2.CAP_PROP_FPS)

    if fps <= 0:
        capture.release()
        raise RuntimeError("No se pudo determinar el FPS")

    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total_frames / fps

    print(f"Analizando: {video_path.name}")
    print(f"FPS: {fps:.2f}")
    print(f"Duración: {duration:.2f} segundos")

    frames_saved = []

    try:
        timestamp = 0.0

        while timestamp < duration:
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000)

            success, frame = capture.read()

            if not success:
                break

            output_path = (
                FRAMES_DIR
                / f"{video_path.stem}_{int(timestamp * 1000):08d}.jpg"
            )

            if not cv2.imwrite(str(output_path), frame):
                raise RuntimeError(
                    f"No se pudo guardar: {output_path}"
                )

            frames_saved.append(output_path)
            print(f"Fotograma extraído: {timestamp:.1f}s")

            timestamp += interval_seconds

    finally:
        capture.release()

    print(f"Total de fotogramas: {len(frames_saved)}")
    return frames_saved


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Uso: python -m backend.analyzer clips/raw/video.mp4")
        sys.exit(1)

    extract_frames(Path(sys.argv[1]).resolve())
