"""Calibrar sobre una imagen congelada; no automatiza controles del juego."""
import argparse
import json
from pathlib import Path
import time
import uuid
import math

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE = ROOT / "data" / "counter-state.json"


def write_phase(path, phase):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"phase": phase, "revision": uuid.uuid4().hex}), encoding="utf-8")
    temporary.replace(path)


def load_calibration(path, monitor):
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("monitor") != monitor or config.get("version") not in (1, 2):
        raise ValueError("La calibración no corresponde al monitor/version")
    roi = config["counter_roi"] if config["version"] == 2 else config["roi"]
    config["roi"] = roi
    if (len(roi) != 4 or not all(type(x) in (float, int) and 0 <= x <= 1 for x in roi)
            or roi[0] >= roi[2] or roi[1] >= roi[3]):
        raise ValueError("ROI de contador inválida")
    resolution = config["resolution"]
    if len(resolution) != 2 or any(type(v) is not int or v <= 0 for v in resolution):
        raise ValueError("Resolución inválida")
    if ((roi[2] - roi[0]) * resolution[0] > 320
            or (roi[3] - roi[1]) * resolution[1] > 160):
        raise ValueError("Selecciona únicamente el número: ROI máximo 320x160")
    if config["version"] == 2:
        confidence = config.get("skull_confidence")
        if (type(confidence) not in (int, float) or not math.isfinite(confidence)
                or not 0 < confidence <= 1):
            raise ValueError("Umbral de calavera inválido")
        skull = config["skull_roi"]
        if (len(skull) != 4 or not all(type(v) in (int, float) and 0 <= v <= 1 for v in skull)
                or skull[0] >= skull[2] or skull[1] >= skull[3]
                or (skull[2] - skull[0]) * resolution[0] > 160
                or (skull[3] - skull[1]) * resolution[1] > 160):
            raise ValueError("ROI de calavera inválida")
        if max(roi[0], skull[0]) < min(roi[2], skull[2]) and max(roi[1], skull[1]) < min(roi[3], skull[3]):
            raise ValueError("Los dos ROIs deben ser independientes, sin solaparse")
        template_path = path.parent / config["skull_template"]
        if not template_path.is_file():
            raise ValueError("No existe la plantilla de calavera")
        config["skull_template_path"] = str(template_path.resolve())
    return config


def select_region(image, label, initial=None, max_width=320):
    import cv2
    height, width = image.shape[:2]
    scale = min(1, 1280 / width, 720 / height)
    preview = cv2.resize(image, (round(width * scale), round(height * scale)))
    if initial:
        cv2.rectangle(preview, (round(preview.shape[1] * initial[0]), round(preview.shape[0] * initial[1])),
                      (round(preview.shape[1] * initial[2]), round(preview.shape[0] * initial[3])), (0, 255, 255), 1)
    print(f"Selecciona SOLO {label}. Enter confirma selección; Esc cancela.")
    x, y, w, h = cv2.selectROI(label + " - imagen congelada", preview, showCrosshair=True)
    cv2.destroyAllWindows()
    if w <= 0 or h <= 0:
        return None
    sx, sy = preview.shape[1] / width, preview.shape[0] / height
    left, top = round(x / sx), round(y / sy)
    right, bottom = min(width, round((x + w) / sx)), min(height, round((y + h) / sy))
    crop = image[top:bottom, left:right]
    if crop.shape[1] > max_width or crop.shape[0] > 160:
        raise ValueError("La región es demasiado grande")
    zoom = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
    cv2.imshow("Verificar " + label + " (Enter confirma / Esc cancela)", zoom)
    key = cv2.waitKey(0) & 0xff
    cv2.destroyAllWindows()
    return ([left / width, top / height, right / width, bottom / height], crop, zoom) if key == 13 else None


def calibrate(args):
    import cv2
    import numpy as np
    import mss
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.image:
        image = cv2.imread(str(args.image))
        if image is None:
            raise ValueError("No se pudo leer la imagen del monitor completo")
    else:
        print(f"Captura en {args.delay}s. Deja visible el HUD del monitor {args.monitor}.")
        time.sleep(args.delay)
        with mss.MSS() as screen:
            if not 1 <= args.monitor < len(screen.monitors):
                raise ValueError("Monitor inexistente")
            image = np.asarray(screen.grab(screen.monitors[args.monitor]))[:, :, :3].copy()
    height, width = image.shape[:2]
    monitor_path = args.output.with_name(args.output.stem + "-monitor.png")
    if not cv2.imwrite(str(monitor_path), image):
        raise OSError("No se pudo guardar la captura completa")
    print(f"Captura completa: {monitor_path} ({width}x{height})")
    skull = select_region(image, "la calavera (con pequeño margen)",
                          getattr(args, "initial_skull_roi", None), max_width=160)
    if skull is None:
        print("Calibración cancelada.")
        return
    number = select_region(image, "el numero de kills (espacio para varios digitos)", getattr(args, "initial_roi", None))
    if number is None:
        print("Calibración cancelada.")
        return
    from backend.hud_detector import SkullMatcher
    threshold = getattr(args, "skull_confidence", .8)
    matcher = SkullMatcher(skull[1], threshold)
    matcher.observe(skull[1])
    print(f"Autocomprobación plantilla: {matcher.observe(skull[1])}. No valida otros fondos del juego.")
    template_path = args.output.with_name(args.output.stem + "-skull.png")
    config = {"version": 2, "monitor": args.monitor, "resolution": [width, height],
              "roi": number[0], "counter_roi": number[0], "skull_roi": skull[0],
              "skull_template": template_path.name, "skull_confidence": threshold}
    if max(number[0][0], skull[0][0]) < min(number[0][2], skull[0][2]) and max(number[0][1], skull[0][1]) < min(number[0][3], skull[0][3]):
        raise ValueError("Los ROIs no deben solaparse")
    for target, pixels in [(template_path, skull[1]),
                            (args.output.with_name(args.output.stem + "-skull-zoom.png"), skull[2]),
                            (args.output.with_name(args.output.stem + "-counter.png"), number[1]),
                            (args.output.with_name(args.output.stem + "-roi-zoom.png"), number[2])]:
        if not cv2.imwrite(str(target), pixels):
            raise OSError("No se pudo guardar un recorte")
    args.output.write_text(json.dumps(config, indent=2), encoding="utf-8")
    cv2.imwrite(str(args.output.with_suffix(".jpg")), number[1])
    print(f"ROI guardada: {args.output}. Resolución: {width}x{height}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("calibrate")
    capture.add_argument("--monitor", type=int, default=2)
    capture.add_argument("--delay", type=float, default=5)
    capture.add_argument("--image", type=Path, help="Alternativa: imagen de todo el monitor, a resolución nativa")
    capture.add_argument("--output", type=Path, default=ROOT / "data" / "counter-roi.json")
    from backend.live_detector import parse_roi
    capture.add_argument("--initial-roi", type=parse_roi, help="Rectángulo orientativo, debe seleccionarse y verificarse")
    capture.add_argument("--initial-skull-roi", type=parse_roi, help="Rectángulo orientativo para la calavera")
    capture.add_argument("--skull-confidence", type=float, default=.8)
    phase = commands.add_parser("phase")
    phase.add_argument("value", choices=["unknown", "warmup", "loading", "match"])
    phase.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args()
    if args.command == "phase":
        write_phase(args.state_file, args.value)
        print(f"Fase: {args.value}")
    else:
        if not math.isfinite(args.delay) or not 0 <= args.delay <= 60:
            parser.error("Delay entre 0 y 60 segundos")
        if not math.isfinite(args.skull_confidence) or not 0 < args.skull_confidence <= 1:
            parser.error("Umbral de calavera entre 0 (exclusivo) y 1")
        calibrate(args)


if __name__ == "__main__":
    main()
