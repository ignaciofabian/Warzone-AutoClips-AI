"""Lógica pura de confirmación y disparo, sin pantalla, OCR ni OBS."""
from collections import deque
from dataclasses import dataclass
import re
import unicodedata

from backend.highlight_detector import MIN_KILLS, MULTIKILL_WINDOW, within_window


def parse_confirmation(text):
    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    # No aceptar puntos, asistencias, derribos ni el feed general.
    match = re.match(r"^\s*(?:eliminado|eliminada|eliminated)\s*[:：]\s*(\S.*)$",
                     normalized, re.IGNORECASE)
    if not match:
        return None
    enemy = " ".join(match[1].split()).casefold()
    return enemy if len(enemy) >= 2 else None


class ConfirmationGate:
    def __init__(self, confirmations=2, max_gap=1.5, dedup_seconds=8,
                 rearm_seconds=2):
        if confirmations < 2 or min(max_gap, dedup_seconds, rearm_seconds) <= 0:
            raise ValueError("Parámetros de confirmación inválidos")
        self.confirmations = confirmations
        self.max_gap = max_gap
        self.dedup_seconds = dedup_seconds
        self.rearm_seconds = rearm_seconds
        self.candidate = None
        self.count = 0
        self.last_sample = None
        self.last_seen = {}
        self.emitted = {}
        self.last_decision = {}

    def decision(self, reason, enemy=None, **fields):
        self.last_decision = {"reason": reason, "enemy": enemy,
                              "count": self.count, "required": self.confirmations,
                              **fields}

    def observe(self, text, second):
        if self.last_sample is not None and second < self.last_sample:
            raise ValueError("El reloj debe ser monótono")
        enemy = parse_confirmation(text)
        contiguous = (self.last_sample is not None
                      and second - self.last_sample <= self.max_gap)
        gap = None if self.last_sample is None else second - self.last_sample
        self.last_sample = second
        # Acotar estado incluso si todo el OCR es ruido sin confirmar eventos.
        stale = [key for key, seen in self.last_seen.items()
                 if second - seen > max(60, self.dedup_seconds)]
        for key in stale:
            self.last_seen.pop(key, None)
            self.emitted.pop(key, None)
        if enemy is None:
            pending_count = self.count
            self.candidate, self.count = None, 0
            self.decision("empty_or_low_confidence" if not text.strip()
                          else "invalid_prefix_colon_or_name", interrupted_count=pending_count)
            return None
        previous_seen = self.last_seen.get(enemy)
        self.last_seen[enemy] = second
        # Un banner persistente no vuelve a disparar aunque venza el TTL.
        if enemy in self.emitted:
            absent = previous_seen is None or second - previous_seen >= self.rearm_seconds
            if second - self.emitted[enemy] < self.dedup_seconds or not absent:
                self.candidate, self.count = None, 0
                self.decision("dedup_ttl" if second - self.emitted[enemy] < self.dedup_seconds
                              else "persistent_banner", enemy)
                return None
            del self.emitted[enemy]
        previous_candidate = self.candidate
        self.count = self.count + 1 if enemy == self.candidate and contiguous else 1
        self.candidate = enemy
        if self.count < self.confirmations:
            reason = "waiting_confirmation"
            if previous_candidate is not None and enemy != previous_candidate:
                reason = "name_changed"
            elif gap is not None and not contiguous:
                reason = "sample_gap"
            self.decision(reason, enemy, previous_candidate=previous_candidate, sample_gap=gap)
            return None
        self.decision("accepted", enemy)
        self.emitted[enemy] = second
        self.candidate, self.count = None, 0
        return {"tipo": "kill", "segundo": second, "enemigo": enemy, "texto": text}


@dataclass
class Trigger:
    due: float
    kills: list


class HighlightTrigger:
    def __init__(self, cooldown=30, postroll=5):
        if cooldown < 0 or postroll < 0:
            raise ValueError("Cooldown y postroll deben ser no negativos")
        self.cooldown = cooldown
        self.postroll = postroll
        self.recent = deque()
        self.pending = None
        self.last_request = float("-inf")

    def add(self, event):
        now = event["segundo"]
        self.recent.append(event)
        while self.recent and now - self.recent[0]["segundo"] > MULTIKILL_WINDOW:
            self.recent.popleft()
        while self.recent and not within_window(list(self.recent)):
            self.recent.popleft()
        if (self.pending is None and now - self.last_request >= self.cooldown
                and len(self.recent) >= MIN_KILLS):
            self.pending = Trigger(now + self.postroll, list(self.recent))
            self.recent.clear()
            return self.pending
        return None

    def take_due(self, second):
        if self.pending is None or second < self.pending.due:
            return None
        trigger, self.pending = self.pending, None
        # También tras errores/timeout: no repetir una orden de resultado incierto.
        self.last_request = second
        self.recent.clear()
        return trigger
