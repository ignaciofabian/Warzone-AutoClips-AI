"""Exporta highlights de una sesión, después de jugar. Tiempos aproximados."""
import argparse
import json
import math
from pathlib import Path
import re
import shutil
import subprocess

from backend.highlight_detector import detectar_highlights

ROOT = Path(__file__).resolve().parent.parent


def load_records(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    records = []
    for index, line in enumerate(lines):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            if index != len(lines) - 1:
                raise
            print("Última línea incompleta ignorada (sesión interrumpida).")
    return records


def video_duration(path):
    import imageio_ffmpeg
    # Solo metadatos: no decodificar el replay entero ni suponer 120s exactos.
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                            capture_output=True, text=True, errors="replace", timeout=30)
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
    if not match:
        raise ValueError("No se pudo determinar la duración del replay")
    duration = int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Duración inválida")
    return duration


def map_highlights(kills, saved_second, duration):
    """El final del replay se aproxima al instante de solicitud; margen revisable."""
    start = saved_second - duration
    covered = []
    for event in kills:
        if not start <= event["segundo"] <= saved_second:
            continue
        bounds = event.get("time_bounds")
        if bounds and not (start <= bounds["start"] <= bounds["end"] <= saved_second):
            continue
        mapped = dict(event, segundo=event["segundo"] - start)
        if bounds:
            mapped["time_bounds"] = {key: value - start for key, value in bounds.items()}
        covered.append(mapped)
    highlights = detectar_highlights(covered)
    for highlight in highlights:
        highlight["fin_clip"] = min(duration, highlight["fin_clip"])
        highlight["sincronizacion"] = "aproximada_revisar"
    return covered, highlights


def export_session(path, *, generate=False, vertical=False):
    records = load_records(path)
    kills = [r for r in records if r.get("kind") == "kill"]
    saves = [r for r in records if r.get("kind") == "replay_saved"]
    used_ids = set()
    exported = []
    for index, save in enumerate(saves, 1):
        source = Path(save["path"])
        if not source.is_file():
            print(f"Replay ausente: {source.name}; no se exporta.")
            continue
        duration = video_duration(source)
        available = [k for k in kills if k["id"] not in used_ids]
        covered, highlights = map_highlights(available, float(save["segundo"]), duration)
        if not highlights:
            continue
        # Nombre por sesión: no sobrescribir un replay original con el mismo nombre.
        name = f"{path.stem}_replay_{index:03d}"
        target = ROOT / "clips" / "raw" / (name + source.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copy2(source, target)
        for directory, payload in (("events", covered), ("highlights", highlights)):
            output = ROOT / "data" / directory / (name + ".json")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        for highlight in highlights:
            used_ids.update(highlight["event_ids"])
        exported.append(target)
        print(f"Exportado: {target.name}; {len(highlights)} highlights para revisión")
        if generate:
            from backend.clip_generator import generar_clips
            from backend.processor import create_reel
            generar_clips(target.name)
            if vertical:
                for number in range(1, len(highlights) + 1):
                    horizontal = ROOT / "clips" / "highlights" / f"{name}_highlight_{number}.mp4"
                    if horizontal.is_file():
                        create_reel(horizontal)
    return exported


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--generate", action="store_true", help="Generar clips horizontales con FFmpeg")
    parser.add_argument("--vertical", action="store_true", help="También generar reels (requiere --generate)")
    args = parser.parse_args()
    if args.vertical and not args.generate:
        parser.error("--vertical requiere --generate")
    session_path = args.session.resolve()
    # Los módulos originales de clips usan rutas relativas al proyecto.
    import os
    os.chdir(ROOT)
    paths = export_session(session_path, generate=args.generate, vertical=args.vertical)
    print(f"Replays exportados: {len(paths)}. Revisa la sincronización antes de publicar.")


if __name__ == "__main__":
    main()
