"""Diagnóstico de muestras y comparación en sombra; nunca dispara OBS."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import statistics
import unicodedata
import re
from backend.live_events import ConfirmationGate, parse_confirmation


def combat_labels(text):
    """Indicios para diagnóstico, nunca evidencia suficiente de kill."""
    normalized = unicodedata.normalize("NFKD", text).upper()
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    normalized = normalized.translate(str.maketrans({"0": "O", "1": "I"}))
    labels = []
    if re.search(r"\b(?:ABATID[OA]S?|DOWNED)\b", normalized):
        labels.append("down")
    if re.search(r"\b(?:ELIMINAD[OA]S?|ELIMINATED)\b", normalized):
        labels.append("elimination")
    return labels


def rejection_details(reading, decision):
    """Explica filtros observables sin relajar el parser activo."""
    details = []
    text, raw = reading["text"], reading["raw_text"]
    match = re.search(r"\b(?:eliminado|eliminada|eliminated)\s*[:：]", text, re.IGNORECASE)
    if match and text[:match.start()].strip():
        details.append("text_before_elimination_prefix")
    if parse_confirmation(raw) is not None and parse_confirmation(text) is None:
        details.append("confidence_filter_removed_strict_candidate")
    if any(not b["accepted"] for b in reading.get("boxes", [])):
        details.append("low_confidence_boxes_present")
    if "down" in combat_labels(raw) and "elimination" not in combat_labels(raw):
        details.append("down_is_not_a_kill")
    if decision.get("interrupted_count", 0):
        details.append("confirmation_interrupted")
    return details


def prefix_match(text, tolerant=False):
    normalized = unicodedata.normalize("NFKD", text).upper()
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    token = re.match(r"^\s*([A-Z0-9]+)(?=\s|[:：]|$)", normalized)
    if not token:
        return False
    words = ("ELIMINADO", "ELIMINADA", "ELIMINATED")
    if token[1] in words:
        return True
    # Una sustitución OCR en la palabra, sin buscar 'elimin' en frases generales.
    return tolerant and any(len(token[1]) == len(word)
                            and sum(a != b for a, b in zip(token[1], word)) == 1
                            for word in words)


class BannerGate:
    """Cuenta episodios de banner, no nombres. Más conservador ante kills seguidas."""
    def __init__(self, confirmations=2, max_gap=1.5, rearm_seconds=2,
                 dedup_seconds=8, tolerant=False):
        if confirmations < 2:
            raise ValueError("Se requieren al menos dos confirmaciones")
        self.confirmations, self.max_gap = confirmations, max_gap
        self.rearm_seconds, self.dedup_seconds = rearm_seconds, dedup_seconds
        self.tolerant = tolerant
        self.last_sample = self.last_present = self.last_emitted = None
        self.absent_since = None
        self.latched = False
        self.count = 0
        self.last_decision = {}

    def observe(self, text, second):
        if self.last_sample is not None and second < self.last_sample:
            raise ValueError("El reloj debe ser monótono")
        previous_sample = self.last_sample
        contiguous = previous_sample is not None and second - previous_sample <= self.max_gap
        self.last_sample = second
        matched = prefix_match(text, self.tolerant)
        reason = "prefix_not_recognized"
        accepted = False
        if not matched:
            # Solo ausencia observada rearma; una pausa sin capturas no basta.
            if self.absent_since is None:
                self.absent_since = second
            if contiguous and second - self.absent_since >= self.rearm_seconds:
                self.latched = False
            if not contiguous:
                self.absent_since = second
            self.count = 0
        else:
            self.absent_since = None
            self.last_present = second
            if self.latched:
                self.count = 0
                reason = "persistent_banner"
            elif self.last_emitted is not None and second - self.last_emitted < self.dedup_seconds:
                self.count = 0
                reason = "dedup_ttl"
            else:
                self.count = self.count + 1 if contiguous else 1
                reason = "waiting_confirmation" if contiguous or previous_sample is None else "sample_gap"
                if self.count >= self.confirmations:
                    self.latched = True
                    self.last_emitted = second
                    reason, accepted = "accepted", True
        self.last_decision = {"reason": reason, "matched": bool(matched),
                              "count": self.count, "required": self.confirmations,
                              "accepted": accepted}
        return accepted


class FailureImages:
    """Un JPEG de la ROI en vuelo, frecuencia y cantidad acotadas."""
    def __init__(self, directory, interval=2, limit=100):
        self.directory, self.interval, self.limit = directory, interval, limit
        self.count = 0
        self.last_attempt = float("-inf")
        self.future = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="diagnostic-image")

    def collect(self, journal):
        if self.future is None or not self.future.done():
            return
        try:
            path, success = self.future.result()
            journal.write("diagnostic_image", path=str(path), written=success)
        except Exception as error:
            journal.write("diagnostic_image", written=False, error_type=type(error).__name__)
        self.future = None

    def submit(self, frame, second, sample_id):
        if self.count >= self.limit:
            return {"status": "limit_reached"}
        if self.future is not None:
            return {"status": "writer_busy"}
        if second - self.last_attempt < self.interval:
            return {"status": "rate_limited"}
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"sample_{sample_id:06d}.jpg"
        def write(image):
            import cv2
            return path, bool(cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 75]))
        self.future = self.pool.submit(write, frame.copy())
        self.count += 1
        self.last_attempt = second
        return {"status": "queued", "path": str(path)}

    def close(self, journal):
        self.pool.shutdown(wait=True)
        self.collect(journal)


def timing_metrics(delta, interval):
    if delta is None:
        return {"capture_gap_seconds": None, "late_seconds": 0, "estimated_missed_slots": 0}
    # Estimación conservadora: intervalos completos extra entre capturas.
    # Un retraso parcial se informa en late_seconds, no como un frame perdido.
    return {"capture_gap_seconds": delta, "late_seconds": max(0, delta - interval),
            "estimated_missed_slots": max(0, math.floor(delta / interval) - 1)}


class Diagnostics:
    def __init__(self, journal, *, confirmations, max_gap, dedup_seconds,
                 interval, image_interval=2, image_limit=100, save_images=False,
                 debug_ocr=False, save_combat_images=False):
        self.journal, self.interval = journal, interval
        self.previous_capture = None
        self.sample_id = 0
        self.debug_ocr = debug_ocr
        self.save_failed_images = save_images
        self.save_combat_images = save_combat_images
        self.capture_region = None
        self.last_down = None
        self.previous_labels = []
        self.gates = {name: BannerGate(confirmations, max_gap, dedup_seconds=dedup_seconds,
                                      tolerant=tolerant)
                      for name, tolerant in (("prefix_exact", False), ("prefix_ocr_tolerant", True))}
        image_directory = "combat" if save_combat_images else "failures"
        self.images = FailureImages(journal.path.with_suffix("") / image_directory,
                                    image_interval, image_limit) if save_images or save_combat_images else None

    def configure_capture(self, *, monitor, resolution, bounds, roi):
        self.capture_region = {"monitor": monitor, "resolution": resolution, "bounds": bounds, "roi": roi}
        if self.debug_ocr:
            print("[DEBUG-OCR REGION] " + json.dumps(self.capture_region, ensure_ascii=False))

    def record(self, reading, second, primary_decision, *, capture_seconds=0,
               frame=None, simulated=False, capture_start=None):
        self.sample_id += 1
        capture_time = second if capture_start is None else capture_start
        delta = None if self.previous_capture is None else capture_time - self.previous_capture
        self.previous_capture = capture_time
        strategies = {"strict_name": dict(primary_decision)}
        for name, gate in self.gates.items():
            gate.observe(reading["text"], second)
            strategies[name] = dict(gate.last_decision)
        raw_matches = {name: bool(prefix_match(reading["raw_text"], tolerant))
                       for name, tolerant in (("prefix_exact", False), ("prefix_ocr_tolerant", True))}
        labels = combat_labels(reading["raw_text"])
        if "down" in labels:
            if "down" not in self.previous_labels:
                self.last_down = {"first_seen_second": second, "last_seen_second": second}
            else:
                self.last_down["last_seen_second"] = second
        combat_context = {"labels": labels, "previous_labels": self.previous_labels,
                          "last_down_observation": dict(self.last_down) if self.last_down else None,
                          "seconds_since_down_text": second - self.last_down["last_seen_second"]
                          if self.last_down and "elimination" in labels else None,
                          "same_enemy_verified": False}
        self.previous_labels = labels
        details = rejection_details(reading, primary_decision)
        if self.images:
            self.images.collect(self.journal)
        image = None
        save_failure = self.save_failed_images and primary_decision.get("reason") not in (
            "accepted", "dedup_ttl", "persistent_banner")
        save_related = self.save_combat_images and bool(labels)
        if frame is not None and self.images and (save_failure or save_related):
            image = self.images.submit(frame, second, self.sample_id)
        if self.debug_ocr:
            print("[DEBUG-OCR] " + json.dumps({
                "sample_id": self.sample_id, "segundo": second,
                "raw_text": reading["raw_text"], "filtered_text": reading["text"],
                "boxes": [{"text": b["text"], "confidence": b["confidence"],
                           "accepted": b["accepted"]} for b in reading.get("boxes", [])],
                "ocr_seconds": reading.get("ocr_seconds"), "capture_region": self.capture_region,
                "ocr_shape": reading.get("ocr_shape"), "decision": primary_decision,
                "details": details, "timing": timing_metrics(delta, self.interval),
                "combat_context": combat_context, "image": image,
            }, ensure_ascii=False))
        self.journal.write("ocr_sample", sample_id=self.sample_id, segundo=second,
                           simulated=simulated, reading=reading, strategies=strategies,
                           raw_prefix_matches=raw_matches, capture_seconds=capture_seconds,
                           capture_start_second=capture_time,
                           combat_context=combat_context, rejection_details=details,
                           filter_effect={
                               "removed_prefix": {name: raw_matches[name] and not gate.last_decision["matched"]
                                                  for name, gate in self.gates.items()},
                               "removed_strict_candidate": bool(parse_confirmation(reading["raw_text"]))
                               and parse_confirmation(reading["text"]) is None},
                           timing=timing_metrics(delta, self.interval), failed_image=image)

    def close(self):
        if self.images:
            self.images.close(self.journal)


def percentile(values, percent):
    return sorted(values)[max(0, math.ceil(len(values) * percent) - 1)] if values else None


def summarize(path):
    reasons, accepted = {}, Counter()
    combat, details = Counter(), Counter()
    counter_reasons, counter_times = Counter(), []
    ocr, gaps, sample_count, missed, low_boxes = [], [], 0, 0, 0
    with path.open(encoding="utf-8") as file:
        for line in file:
            try:
                sample = json.loads(line)
            except json.JSONDecodeError:
                continue
            if sample.get("kind") == "counter_sample":
                counter_reasons[sample["decision"]["reason"]] += 1
                if sample["reading"].get("ocr_seconds") is not None:
                    counter_times.append(sample["reading"]["ocr_seconds"])
            if sample.get("kind") != "ocr_sample":
                continue
            sample_count += 1
            reading = sample["reading"]
            combat.update(sample.get("combat_context", {}).get("labels", combat_labels(reading["raw_text"])))
            details.update(sample.get("rejection_details", []))
            if reading.get("ocr_seconds") is not None:
                ocr.append(reading["ocr_seconds"])
            gap = sample["timing"]["capture_gap_seconds"]
            if gap is not None:
                gaps.append(gap)
            missed += sample["timing"]["estimated_missed_slots"]
            low_boxes += sum(not box["accepted"] for box in reading.get("boxes", []))
            for name, decision in sample["strategies"].items():
                reasons.setdefault(name, Counter())[decision["reason"]] += 1
                if decision["reason"] == "accepted":
                    accepted[name] += 1
    def stats(values):
        return {"median": statistics.median(values) if values else None,
                "p95": percentile(values, 0.95), "max": max(values) if values else None}
    return {"samples": sample_count, "ocr_seconds": stats(ocr), "capture_gap_seconds": stats(gaps),
            "estimated_missed_slots": missed, "low_confidence_boxes": low_boxes,
            "accepted_by_strategy": {name: accepted[name] for name in reasons},
            "combat_text_samples": dict(combat), "rejection_details": dict(details),
            "counter_reasons": dict(counter_reasons), "counter_ocr_seconds": stats(counter_times),
            "reasons": {name: dict(counts) for name, counts in reasons.items()},
            "note": "Slots de muestreo estimados; no mide frames del juego ni confirma kills reales."}


def recheck(path, confirmations=None, confidence=None):
    """Reevaluar el OCR registrado offline, sin volver a cargar modelos ni imágenes."""
    config, gates = {}, None
    reasons, events = {}, {}
    with path.open(encoding="utf-8") as file:
        for line in file:
            try:
                sample = json.loads(line)
            except json.JSONDecodeError:
                continue
            if sample.get("kind") == "session_start":
                config = sample
            if sample.get("kind") != "ocr_sample":
                continue
            if gates is None:
                if confirmations is None:
                    confirmations = config.get("confirmations", 2)
                kwargs = {"confirmations": confirmations, "max_gap": config.get("max_gap", 1.5),
                          "dedup_seconds": config.get("dedup", 8)}
                gates = {"strict_name": ConfirmationGate(**kwargs),
                         "prefix_exact": BannerGate(**kwargs),
                         "prefix_ocr_tolerant": BannerGate(**kwargs, tolerant=True)}
                reasons = {name: Counter() for name in gates}
                events = {name: [] for name in gates}
            reading = sample["reading"]
            text = reading["text"]
            if confidence is not None:
                if not reading.get("boxes") and reading.get("raw_text"):
                    raise ValueError("La muestra simulada no tiene confianzas para reevaluar")
                text = " ".join(box["text"] for box in reading["boxes"]
                                if box["confidence"] >= confidence)
            for name, gate in gates.items():
                gate.observe(text, sample["segundo"])
                reason = gate.last_decision["reason"]
                reasons[name][reason] += 1
                if reason == "accepted":
                    events[name].append(sample["segundo"])
    return {"confirmations": confirmations, "confidence_override": confidence,
            "accepted_seconds": events, "reasons": {name: dict(c) for name, c in reasons.items()},
            "note": "Comparación offline de las mismas muestras; no estima muestras que no se capturaron."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--recheck-confirmations", type=int, help="Reevaluar lecturas con 2 o más confirmaciones")
    parser.add_argument("--recheck-confidence", type=float, help="Reevaluar las cajas almacenadas con otro umbral")
    args = parser.parse_args()
    if args.recheck_confirmations is not None and args.recheck_confirmations < 2:
        parser.error("No se permiten menos de dos confirmaciones")
    if args.recheck_confidence is not None and not 0 <= args.recheck_confidence <= 1:
        parser.error("La confianza debe estar entre 0 y 1")
    report = summarize(args.session)
    if args.recheck_confirmations is not None or args.recheck_confidence is not None:
        report["recheck"] = recheck(args.session, args.recheck_confirmations,
                                    args.recheck_confidence)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
