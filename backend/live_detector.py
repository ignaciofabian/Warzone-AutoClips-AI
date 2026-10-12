"""Captura pasiva de una ROI y OCR; ninguna interacción con el juego."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import uuid
from collections import deque

from backend.live_events import ConfirmationGate, HighlightTrigger

ROOT = Path(__file__).resolve().parent.parent


class Journal:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                                 + "_" + uuid.uuid4().hex[:8] + ".jsonl")
        self.file = self.path.open("x", encoding="utf-8")

    def write(self, kind, **fields):
        record = {"kind": kind, "utc": datetime.now(timezone.utc).isoformat(), **fields}
        self.file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()


class Session:
    def __init__(self, journal, gate, trigger, dry_run, diagnostics=None, counter=None):
        self.journal, self.gate, self.trigger = journal, gate, trigger
        self.dry_run = dry_run
        self.diagnostics = diagnostics
        self.counter = counter
        self.counter_events = deque()
        self.last_counter_reason = None

    def reset_round(self, reason):
        self.counter_events.clear()
        self.gate = ConfirmationGate(self.gate.confirmations, self.gate.max_gap,
                                     self.gate.dedup_seconds, self.gate.rearm_seconds)
        self.trigger.recent.clear()
        if self.trigger.pending:
            self.journal.write("highlight_cancelled", reason=reason)
        self.trigger.pending = None

    def emit(self, event):
        event["id"] = uuid.uuid4().hex
        self.journal.write("kill", **event)
        print(f"Kill candidata confirmada: {event['enemigo']} ({event['segundo']:.1f}s)")
        scheduled = self.trigger.add(event)
        if scheduled:
            self.journal.write("highlight_scheduled", due=scheduled.due,
                               event_ids=[e["id"] for e in scheduled.kills])

    def due(self, second):
        due = self.trigger.take_due(second)
        if due:
            self.journal.write("replay_request", segundo=second, dry_run=self.dry_run,
                               event_ids=[e["id"] for e in due.kills])
        return due

    def observe_counter(self, reading, second):
        epoch = self.counter.epoch
        events = self.counter.observe(reading["value"], reading["confidence"], second,
                                      hud_visible=reading.get("hud_visible"))
        if self.counter.last_decision["reason"] == "hud_hidden" and self.last_counter_reason != "hud_hidden":
            self.reset_round("hud_hidden")
            print("[KILL-COUNTER] HUD oculto: total conservado, OCR suspendido.")
        if (self.counter.last_decision["reason"] == "counter_reset"
                or self.counter.last_decision["reference_expired"]):
            self.counter.set_phase("unknown")
            self.counter.last_decision["requires_phase_confirmation"] = True
            self.counter.last_decision["effective_phase"] = "unknown"
            print("Referencia de contador invalidada: confirma fase match antes de contar de nuevo.")
        if epoch != self.counter.epoch:
            self.reset_round("counter_reference_changed")
        self.journal.write("counter_sample", segundo=second, reading=reading,
                           decision=self.counter.last_decision)
        decision = self.counter.last_decision
        reason = decision["reason"]
        if events:
            print(f"[KILL-COUNTER] anterior={decision['previous']} actual={decision['new']} "
                  f"nuevas={len(events)} validado=true intervalo={decision['time_bounds']}")
        elif reason != self.last_counter_reason:
            if reason == "invalid_or_low_confidence":
                cause = "numero_no_reconocido" if reading["value"] is None else "baja_confianza_o_fuera_de_rango"
                print(f"[KILL-COUNTER] lectura_invalida motivo={cause}")
            elif reason == "waiting_reset":
                print(f"[KILL-COUNTER] posible_reinicio anterior={decision['previous']} actual=0")
            elif reason == "counter_reset":
                print("[KILL-COUNTER] reinicio_confirmado contador=0 requiere_fase_match=true")
            elif reason == "baseline_established":
                label = "nueva_partida_confirmada" if decision["new"] == 0 else "referencia_confirmada"
                print(f"[KILL-COUNTER] {label} contador={decision['new']} origen=fase_explicita")
            elif reason == "implausible_change":
                print(f"[KILL-COUNTER] lectura_invalida motivo=cambio_no_plausible anterior={decision['previous']} actual={decision['new']}")
        self.last_counter_reason = reason
        for event in events:
            self.emit(event)
            self.counter_events.append(event)
        while self.counter_events and second - self.counter_events[0]["segundo"] > 8:
            self.counter_events.popleft()
        return events

    def observe(self, text, second):
        event = self.gate.observe(text, second)
        if event:
            if self.counter is None:
                self.emit(event)
            else:
                while self.counter_events and second - self.counter_events[0]["segundo"] > 8:
                    self.counter_events.popleft()
                primary = self.counter_events.popleft() if self.counter_events else None
                self.journal.write("secondary_evidence", segundo=second, texto=text,
                                   enemy=event["enemigo"], event_id=primary["id"] if primary else None,
                                   association="temporal_only" if primary else "no_counter_event",
                                   counted_as_new_kill=False)
        return self.due(second)


def roi_bounds(monitor, roi):
    x1, y1, x2, y2 = roi
    left, top = int(monitor["width"] * x1), int(monitor["height"] * y1)
    right, bottom = int(monitor["width"] * x2), int(monitor["height"] * y2)
    if right <= left or bottom <= top:
        raise ValueError("La ROI no tiene píxeles")
    return {"left": monitor["left"] + left, "top": monitor["top"] + top,
            "width": right - left, "height": bottom - top}


def run_simulation(args, session):
    observations = json.loads(args.simulate.read_text(encoding="utf-8"))
    for item in observations:
        second = float(item["segundo"])
        if not math.isfinite(second) or second < 0:
            raise ValueError("Timestamp inválido")
        if session.counter:
            if "phase" in item:
                session.counter.set_phase(item["phase"])
                session.reset_round("phase_changed")
            if "counter" in item:
                session.observe_counter(item["counter"], second)
        text = item.get("reading", {}).get("text", item.get("texto", ""))
        session.observe(text, second)
        if session.diagnostics:
            # Una fixture puede proporcionar cajas/confianzas de una lectura real.
            reading = item.get("reading", {"text": text, "raw_text": text,
                                          "ocr_seconds": None, "boxes": []})
            session.diagnostics.record(reading, second, session.gate.last_decision, simulated=True)


def run_live(args, session):
    import mss
    import numpy as np
    from backend.kill_detector import get_reader, leer_confirmacion
    from backend.obs_controller import ReplayController
    from backend.counter_detector import read_counter

    # Deshabilitar logs del SDK: sus mensajes pueden incluir autenticación.
    import logging
    logging.getLogger("obsws_python").disabled = True
    logging.getLogger("obsws_python").setLevel(logging.CRITICAL + 1)
    get_reader(download_enabled=False, threads=args.threads)
    controller = ReplayController()
    origin = time.monotonic()
    future = None
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="obs-save")

    def save():
        path = controller.save()
        second = controller.requested_at - origin
        return second, path

    def collect():
        nonlocal future
        if future is None:
            return
        try:
            second, path = future.result()
            session.journal.write("replay_saved", segundo=second, path=path,
                                  timing="estimated_request_time")
            print(f"Replay confirmado: {Path(path).name}")
        except Exception as error:
            session.journal.write("replay_error", error_type=type(error).__name__,
                                  resultado="incierto_no_reintentar")
            print("OBS no confirmó el guardado. Revisa el registro; no se reenvía la orden.")
        future = None

    try:
        if not args.dry_run:
            controller.connect()
        with mss.mss() as screen:
            if not 1 <= args.monitor < len(screen.monitors):
                raise ValueError("Monitor inexistente")
            bounds = roi_bounds(screen.monitors[args.monitor], args.roi)
            monitor = screen.monitors[args.monitor]
            counter_bounds = None
            skull_bounds = skull_matcher = None
            secondary_until, next_secondary = float("-inf"), 0
            phase_revision = None
            if session.counter:
                config = args.counter_calibration
                if config["resolution"] != [monitor["width"], monitor["height"]]:
                    raise ValueError("Cambió la resolución: vuelve a calibrar el contador")
                counter_bounds = roi_bounds(monitor, config["roi"])
                if config.get("version") == 2:
                    import cv2
                    from backend.hud_detector import SkullMatcher
                    cv2.setNumThreads(args.threads)
                    threshold = args.skull_confidence if args.skull_confidence is not None else config["skull_confidence"]
                    skull_matcher = SkullMatcher(cv2.imread(config["skull_template_path"]), threshold,
                                                 args.skull_confirmations)
                    skull_bounds = roi_bounds(monitor, config["skull_roi"])
                    session.journal.write("skull_config", bounds=skull_bounds, threshold=threshold,
                                          confirmations=args.skull_confirmations)
                session.journal.write("counter_config", bounds=counter_bounds, calibration=config)
                # Descartar una fase persistida de otra ejecución.
                from backend.counter_calibration import write_phase
                write_phase(args.phase_file, args.phase)
                print(f"Contador activo; fase inicial {args.phase}. Control: {args.phase_file}")
            if session.diagnostics:
                session.diagnostics.configure_capture(
                    monitor=args.monitor, resolution=[monitor["width"], monitor["height"]],
                    bounds=bounds, roi=args.roi)
            session.journal.write("capture_config", monitor=args.monitor, bounds=bounds,
                                  resolution=[monitor["width"], monitor["height"]],
                                  roi=args.roi, interval=args.interval, threads=args.threads)
            print("Captura iniciada. Ctrl+C para detener." +
                  (" Sin órdenes a OBS." if args.dry_run else " No cierres OBS durante el guardado."))
            while True:
                started = time.monotonic()
                if counter_bounds is not None:
                    try:
                        state = json.loads(args.phase_file.read_text(encoding="utf-8"))
                        if state.get("revision") != phase_revision:
                            phase_revision = state.get("revision")
                            changed = session.counter.set_phase(state["phase"])
                            if not changed:
                                session.counter.invalidate()
                            session.reset_round("phase_changed")
                            secondary_until = float("-inf")
                            session.journal.write("phase_changed", phase=state["phase"])
                    except (OSError, ValueError, KeyError):
                        if session.counter.set_phase("unknown"):
                            session.reset_round("phase_control_unavailable")
                        secondary_until = float("-inf")
                    skull_reading = None
                    if skull_matcher:
                        skull_frame = np.asarray(screen.grab(skull_bounds))[:, :, :3].copy()
                        skull_reading = skull_matcher.observe(skull_frame)
                        session.journal.write("skull_sample", segundo=time.monotonic() - origin, **skull_reading)
                    if skull_reading is None or skull_reading["visible"]:
                        counter_frame = np.asarray(screen.grab(counter_bounds))[:, :, :3].copy()
                        counter_reading = read_counter(counter_frame, preprocessing=args.counter_preprocess)
                    else:
                        counter_reading = {"value": None, "confidence": 0, "ocr_seconds": 0,
                                           "raw_text": "", "skipped": "icon_not_visible"}
                    second = time.monotonic() - origin
                    if skull_reading:
                        counter_reading["hud_visible"] = skull_reading["visible"]
                        counter_reading["skull"] = skull_reading
                    events = session.observe_counter(counter_reading, second)
                    if session.counter.phase != "match" or (skull_reading and not skull_reading["visible"]):
                        secondary_until = float("-inf")
                    if getattr(args, "debug_ocr", False):
                        print("[DEBUG-COUNTER] " + json.dumps({"segundo": second, "bounds": counter_bounds,
                              "skull_bounds": skull_bounds,
                              "reading": counter_reading, "decision": session.counter.last_decision}))
                    if events:
                        secondary_until = second + 3
                    # OCR inferior solo durante un breve período tras incrementos.
                    # El contador acumulativo recupera saltos durante este trabajo adicional.
                    if not (second <= secondary_until and second >= next_secondary):
                        if future is not None and future.done():
                            collect()
                        due = session.due(second)
                        if due and not args.dry_run:
                            if future is None:
                                future = pool.submit(save)
                            else:
                                session.journal.write("replay_skipped", reason="save_in_progress")
                        cost = time.monotonic() - started
                        if cost > args.interval:
                            session.journal.write("sample_slow", segundo=second, seconds=cost, source="counter")
                        time.sleep(max(.01, args.interval - cost))
                        continue
                    next_secondary = second + args.secondary_interval
                frame = np.asarray(screen.grab(bounds))[:, :, :3].copy()
                captured_at = time.monotonic()
                second = captured_at - origin
                if session.diagnostics:
                    reading = leer_confirmacion(frame, min_confidence=args.confidence,
                                                max_width=args.ocr_max_width, detailed=True)
                    text = reading["text"]
                else:
                    text = leer_confirmacion(frame, min_confidence=args.confidence,
                                             max_width=getattr(args, "ocr_max_width", 640))
                if future is not None and future.done():
                    collect()
                due = session.observe(text, second)
                if session.diagnostics:
                    session.diagnostics.record(reading, second, session.gate.last_decision,
                                               capture_seconds=captured_at - started,
                                               capture_start=started - origin, frame=frame)
                if due and not args.dry_run:
                    if future is None:
                        future = pool.submit(save)
                    else:
                        session.journal.write("replay_skipped", reason="save_in_progress")
                cost = time.monotonic() - started
                if cost > args.interval:
                    # No cola de frames ni intentos de recuperar muestras perdidas.
                    session.journal.write("sample_slow", segundo=second, seconds=cost)
                time.sleep(max(0.01, args.interval - cost))
    finally:
        pool.shutdown(wait=True, cancel_futures=False)
        collect()
        controller.close()


def parse_roi(value):
    try:
        roi = tuple(float(x) for x in value.split(","))
        if len(roi) != 4 or not all(math.isfinite(x) and 0 <= x <= 1 for x in roi):
            raise ValueError
        if roi[0] >= roi[2] or roi[1] >= roi[3]:
            raise ValueError
        return roi
    except ValueError:
        raise argparse.ArgumentTypeError("Usa x1,y1,x2,y2 entre 0 y 1") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="OCR y registro sin guardar en OBS")
    parser.add_argument("--simulate", type=Path, help="Observaciones JSON; siempre sin OBS ni OCR")
    parser.add_argument("--prepare-ocr", action="store_true", help="Descargar/cargar modelos antes de jugar")
    parser.add_argument("--monitor", type=int, default=1)
    parser.add_argument("--roi", type=parse_roi, default=(0.36, 0.86, 0.62, 0.98))
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--confidence", type=float, default=0.65)
    parser.add_argument("--ocr-max-width", type=int, default=640, help="Ancho OCR máximo, 128–640 px")
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--confirmations", type=int, default=2)
    parser.add_argument("--dedup", type=float, default=8)
    parser.add_argument("--cooldown", type=float, default=30)
    parser.add_argument("--postroll", type=float, default=5)
    parser.add_argument("--log-dir", type=Path, default=ROOT / "data" / "sessions")
    parser.add_argument("--diagnostics", action="store_true", help="Registrar cada OCR y comparar estrategias; implica --dry-run")
    parser.add_argument("--debug-ocr", action="store_true", help="Mostrar cada OCR, región y rechazo; implica --diagnostics y --dry-run")
    parser.add_argument("--save-combat-images", action="store_true", help="Guardar ROI al leer ABATIDO/ELIMINADO, incluso aceptados (requiere diagnóstico)")
    parser.add_argument("--save-failed-images", action="store_true", help="Guardar ROI de muestras sin confirmar (requiere --diagnostics)")
    parser.add_argument("--failed-image-interval", type=float, default=2)
    parser.add_argument("--max-failed-images", type=int, default=100)
    parser.add_argument("--counter-config", type=Path, help="Activa contador principal con ROI calibrada")
    parser.add_argument("--counter-confidence", type=float, default=.85)
    parser.add_argument("--counter-confirmations", type=int, default=2)
    parser.add_argument("--counter-max-jump", type=int, default=5)
    parser.add_argument("--counter-preprocess", choices=["otsu", "gray"], default="otsu")
    parser.add_argument("--skull-confidence", type=float, help="Umbral de similitud del icono, por defecto el calibrado")
    parser.add_argument("--skull-confirmations", type=int, default=2)
    parser.add_argument("--secondary-interval", type=float, default=1)
    parser.add_argument("--phase", choices=["unknown", "loading", "warmup", "match"], default="unknown")
    parser.add_argument("--phase-file", type=Path, default=ROOT / "data" / "counter-state.json")
    args = parser.parse_args()
    if (args.skull_confirmations < 2 or (args.skull_confidence is not None
            and (not math.isfinite(args.skull_confidence) or not 0 < args.skull_confidence <= 1))):
        parser.error("Umbral del icono entre 0 y 1; dos confirmaciones como mínimo")
    numbers = [args.interval, args.confidence, args.dedup, args.cooldown, args.postroll,
               args.failed_image_interval, args.counter_confidence, args.secondary_interval]
    if (not all(math.isfinite(x) for x in numbers) or args.interval < 0.2
            or not 0 <= args.confidence <= 1 or args.threads < 1
            or args.confirmations < 2 or args.dedup <= 0 or args.cooldown < 0
            or args.postroll < 0 or not 128 <= args.ocr_max_width <= 640
            or args.failed_image_interval < 1 or args.max_failed_images < 1
            or not 0 <= args.counter_confidence <= 1 or args.counter_confirmations < 2
            or args.counter_max_jump < 1 or args.secondary_interval < .5):
        parser.error("Parámetros fuera de rango (interval mínimo: 0.2 segundos)")
    if args.debug_ocr:
        args.diagnostics = True
    if (args.save_failed_images or args.save_combat_images) and not args.diagnostics:
        parser.error("Guardar imágenes requiere --diagnostics o --debug-ocr")
    if args.diagnostics:
        args.dry_run = True
    if args.prepare_ocr:
        from backend.kill_detector import get_reader
        get_reader(download_enabled=True, threads=args.threads)
        print("Modelos OCR preparados. Esto no verifica detección en Warzone.")
        return 0
    counter = None
    if args.counter_config:
        from backend.counter_calibration import load_calibration
        from backend.counter_detector import CounterGate
        try:
            args.counter_calibration = load_calibration(args.counter_config, args.monitor)
            if args.counter_calibration["version"] != 2:
                raise ValueError("Recalibra seleccionando calavera y contador")
        except (OSError, ValueError, KeyError) as error:
            parser.error(f"Calibración inválida ({type(error).__name__}). Ejecuta counter_calibration.")
        counter = CounterGate(confirmations=args.counter_confirmations,
                              reset_confirmations=max(4, args.counter_confirmations),
                              confidence=args.counter_confidence, max_jump=args.counter_max_jump,
                              max_gap=max(1.5, args.interval * 3))
        counter.set_phase(args.phase)
    journal = Journal(args.log_dir)
    max_gap = max(1.5, args.interval * 3)
    gate = ConfirmationGate(args.confirmations, max_gap=max_gap,
                            dedup_seconds=args.dedup)
    session = Session(journal, gate, HighlightTrigger(args.cooldown, args.postroll),
                      args.dry_run or bool(args.simulate), counter=counter)
    if args.diagnostics:
        from backend.diagnostics import Diagnostics
        session.diagnostics = Diagnostics(journal, confirmations=args.confirmations,
                                          max_gap=max_gap, dedup_seconds=args.dedup,
                                          interval=args.interval, save_images=args.save_failed_images,
                                          image_interval=args.failed_image_interval,
                                          image_limit=args.max_failed_images,
                                          debug_ocr=args.debug_ocr,
                                          save_combat_images=args.save_combat_images)
        print("Diagnóstico activo: estrategias en sombra; sin órdenes a OBS.")
    journal.write("session_start", dry_run=session.dry_run, simulated=bool(args.simulate),
                  diagnostics=args.diagnostics, confirmations=args.confirmations,
                  cooldown=args.cooldown, dedup=args.dedup, interval=args.interval,
                  confidence=args.confidence, max_gap=max_gap, roi=args.roi,
                  ocr_max_width=args.ocr_max_width, debug_ocr=args.debug_ocr,
                  save_combat_images=args.save_combat_images, source="counter" if counter else "banner",
                  counter_confirmations=args.counter_confirmations,
                  counter_confidence=args.counter_confidence, phase=args.phase)
    print(f"Registro: {journal.path}")
    result = 0
    try:
        if args.simulate:
            run_simulation(args, session)
        else:
            run_live(args, session)
    except KeyboardInterrupt:
        print("Detector detenido.")
    except Exception as error:
        journal.write("session_error", error_type=type(error).__name__)
        print(f"No se pudo continuar ({type(error).__name__}). Revisa instalación, modelos y OBS.")
        result = 1
    finally:
        if session.diagnostics:
            session.diagnostics.close()
        if session.trigger.pending:
            journal.write("highlight_cancelled", reason="session_stopped_before_postroll")
        journal.write("session_stop")
        journal.close()
    if args.diagnostics:
        from backend.diagnostics import summarize
        print(json.dumps(summarize(journal.path), ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
