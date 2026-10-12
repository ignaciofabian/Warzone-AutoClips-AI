"""Contador principal: lógica temporal pura y OCR numérico acotado."""
import math
import re
import time


class CounterGate:
    def __init__(self, confirmations=2, reset_confirmations=4, confidence=.85,
                 max_count=100, max_jump=5, max_gap=1.5, missing_timeout=3):
        if confirmations < 2 or reset_confirmations < confirmations:
            raise ValueError("Confirmaciones inválidas")
        self.confirmations, self.reset_confirmations = confirmations, reset_confirmations
        self.confidence, self.max_count, self.max_jump = confidence, max_count, max_jump
        self.max_gap, self.missing_timeout = max_gap, missing_timeout
        self.phase = "unknown"
        self.epoch = 0
        self.baseline = None
        self.candidate = None
        self.count = 0
        self.last_sample = self.last_valid = None
        self.last_baseline_seen = self.candidate_since = None
        self.last_decision = {}
        self.hud_suspended = False

    def invalidate(self):
        self.baseline = self.candidate = self.last_valid = None
        self.count = 0
        self.last_baseline_seen = self.candidate_since = None
        self.epoch += 1
        self.hud_suspended = False

    def set_phase(self, phase):
        if phase not in {"unknown", "loading", "warmup", "match"}:
            raise ValueError("Fase inválida")
        if phase != self.phase:
            self.phase = phase
            self.invalidate()
            return True
        return False

    def observe(self, value, confidence, second, hud_visible=None):
        if not math.isfinite(second) or (self.last_sample is not None and second < self.last_sample):
            raise ValueError("Reloj inválido")
        gap = None if self.last_sample is None else second - self.last_sample
        previous = self.baseline
        self.last_sample = second
        expired = (hud_visible is None and self.last_valid is not None
                   and second - self.last_valid > self.missing_timeout)
        if expired:
            self.invalidate()
        difference = value - self.baseline if type(value) is int and self.baseline is not None else None
        decision = {"previous": previous, "new": value, "difference": difference,
                    "confidence": confidence, "phase": self.phase, "epoch": self.epoch,
                    "reference_expired": expired, "sample_gap": gap}
        def finish(reason, events=None):
            decision.update(reason=reason, count=self.count, baseline=self.baseline)
            self.last_decision = decision
            return events or []
        if hud_visible is False:
            self.hud_suspended = True
            self.candidate, self.count = None, 0
            decision["hud_visible"] = False
            return finish("hud_hidden")
        decision["hud_visible"] = hud_visible
        decision["hud_recovery"] = self.hud_suspended
        if self.phase != "match":
            self.candidate, self.count = None, 0
            return finish("phase_not_match")
        if (type(value) is not int or not 0 <= value <= self.max_count
                or not math.isfinite(confidence) or confidence < self.confidence):
            self.candidate, self.count = None, 0
            if hud_visible is True:
                self.hud_suspended = True
            return finish("invalid_or_low_confidence")
        if difference is not None and (difference > self.max_jump or (difference < 0 and value != 0)):
            self.candidate, self.count = None, 0
            return finish("implausible_change")
        self.last_valid = second
        if self.baseline == value:
            if self.hud_suspended:
                continuing = self.candidate == value and gap is not None and gap <= self.max_gap
                self.count = self.count + 1 if continuing else 1
                self.candidate = value
                if self.count < max(3, self.confirmations):
                    return finish("waiting_hud_recovery")
                self.hud_suspended = False
            self.last_baseline_seen = second
            self.candidate, self.count = None, 0
            return finish("unchanged")
        continuing = value == self.candidate and gap is not None and gap <= self.max_gap
        if not continuing:
            self.candidate_since = second
        self.count = self.count + 1 if continuing else 1
        self.candidate = value
        required = self.reset_confirmations if self.baseline is not None and value == 0 else self.confirmations
        if self.hud_suspended and self.baseline is not None:
            required = max(3, required)
        decision["required"] = required
        if self.count < required:
            return finish("waiting_reset" if required == self.reset_confirmations else "waiting_confirmation")
        self.candidate, self.count = None, 0
        self.hud_suspended = False
        if self.baseline is None:
            self.baseline = value
            self.last_baseline_seen = second
            return finish("baseline_established")
        if value == 0:
            self.epoch += 1
            self.baseline = 0
            self.last_baseline_seen = second
            decision["epoch"] = self.epoch
            return finish("counter_reset")
        self.baseline = value
        bounds = {"start": self.last_baseline_seen, "end": self.candidate_since}
        decision["time_bounds"] = bounds
        events = [{"tipo": "kill", "segundo": second, "enemigo": "desconocido",
                   "source": "counter", "counter_index": index, "counter_epoch": self.epoch,
                   "timing": "counter_confirmation_estimate", "time_bounds": dict(bounds),
                   "individual_time_observed": False}
                  for index in range(previous + 1, value + 1)]
        self.last_baseline_seen = second
        return finish("accepted_increment", events)


def read_counter(image, preprocessing="otsu"):
    """Una caja fija: evita el detector de texto CRAFT y restringe a dígitos."""
    import cv2
    from backend.kill_detector import get_reader
    start = time.perf_counter()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if preprocessing == "otsu":
        _, gray = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    elif preprocessing != "gray":
        raise ValueError("Preprocesamiento inválido")
    height, width = gray.shape
    scale = min(48 / height, 160 / width)
    gray = cv2.resize(gray, (max(1, round(width * scale)), max(1, round(height * scale))))
    h, w = gray.shape
    results = get_reader(download_enabled=False).recognize(
        gray, horizontal_list=[[0, w, 0, h]], free_list=[], allowlist="0123456789",
        detail=1, paragraph=False, decoder="greedy", batch_size=1, workers=0)
    raw = " ".join(str(row[1]) for row in results)
    text = raw.strip()
    value = int(text) if len(results) == 1 and re.fullmatch(r"[0-9]{1,3}", text) else None
    confidence = float(results[0][2]) if len(results) == 1 else 0.0
    return {"value": value, "confidence": confidence, "raw_text": raw,
            "ocr_seconds": time.perf_counter() - start, "ocr_shape": [h, w],
            "preprocessing": preprocessing}
