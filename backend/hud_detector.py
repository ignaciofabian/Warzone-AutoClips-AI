"""Comprobación de un icono calibrado, en CPU y sin OCR."""
import math
import time


class SkullMatcher:
    def __init__(self, template, confidence=.8, confirmations=2):
        import cv2
        import numpy as np
        if not math.isfinite(confidence) or not 0 < confidence <= 1 or confirmations < 2:
            raise ValueError("Parámetros del icono inválidos")
        if template is None or template.size == 0:
            raise ValueError("No se pudo leer la plantilla de calavera")
        self.confidence, self.confirmations = confidence, confirmations
        edges = self.edges(template)
        # Margen de dos píxeles para buscar desplazamientos mínimos dentro del ROI.
        self.template = edges[2:-2, 2:-2].copy()
        if min(self.template.shape, default=0) < 4 or np.std(self.template) < 1:
            raise ValueError("La plantilla de calavera está vacía o no tiene bordes")
        self.count = 0

    @staticmethod
    def edges(image):
        import cv2
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        return cv2.Canny(gray, 80, 160)

    def observe(self, image):
        import cv2
        import numpy as np
        start = time.perf_counter()
        edges = self.edges(image)
        score = 0.0
        if (edges.shape[0] >= self.template.shape[0] and edges.shape[1] >= self.template.shape[1]
                and np.std(edges) >= 1):
            score = min(1.0, max(0.0, float(cv2.minMaxLoc(cv2.matchTemplate(
                edges, self.template, cv2.TM_CCOEFF_NORMED))[1])))
        matched = math.isfinite(score) and score >= self.confidence
        self.count = min(self.count + 1, self.confirmations) if matched else 0
        visible = self.count >= self.confirmations
        return {"score": score, "threshold": self.confidence, "matched": matched,
                "visible": visible, "confirmations": min(self.count, self.confirmations),
                "reason": "visible" if visible else "waiting_icon_confirmation" if matched else "icon_missing",
                "seconds": time.perf_counter() - start}
