
from pathlib import Path
import sys
import time

import cv2


# Inicializamos OCR una sola vez.
# CPU evita problemas de compatibilidad CUDA al comenzar.
reader = None


def get_reader(*, download_enabled=True, threads=1):
    """Carga diferida: los imports y las pruebas no descargan modelos."""
    global reader
    if reader is None:
        import torch
        import easyocr

        torch.set_num_threads(threads)
        cv2.setNumThreads(threads)
        reader = easyocr.Reader(
            ["es", "en"], gpu=False, download_enabled=download_enabled
        )
    return reader


def leer_confirmacion(zona, *, min_confidence=0.65, max_width=640, detailed=False):
    """OCR acotado de una ROI BGR; sin GPU ni descarga durante la partida."""
    started = time.perf_counter()
    original_shape = list(zona.shape[:2])
    if zona.shape[1] > max_width:
        scale = max_width / zona.shape[1]
        zona = cv2.resize(zona, None, fx=scale, fy=scale)
    results = get_reader(download_enabled=False).readtext(
        zona, detail=1, batch_size=1, workers=0, paragraph=False
    )
    # EasyOCR puede separar el prefijo y el nombre en cajas distintas.
    confident = [str(text) for _, text, confidence in results
                 if confidence >= min_confidence]
    text = " ".join(confident)
    if not detailed:
        return text
    return {
        "text": text,
        "raw_text": " ".join(str(text) for _, text, _ in results),
        "ocr_seconds": time.perf_counter() - started,
        "input_shape": original_shape,
        "ocr_shape": list(zona.shape[:2]),
        "min_confidence": min_confidence,
        "boxes": [{"text": str(text), "confidence": float(confidence),
                   "accepted": bool(confidence >= min_confidence),
                   "bbox": [[float(x), float(y)] for x, y in bbox]}
                  for bbox, text, confidence in results],
    }


def detectar_kills(ruta_imagen):
    imagen = cv2.imread(str(ruta_imagen))

    if imagen is None:
        print(f"No se pudo abrir: {ruta_imagen}")
        return []

    alto, ancho = imagen.shape[:2]

    # Zona amarilla de puntos/eliminaciones (centro-derecha)
    x1, y1 = int(ancho * 0.59), int(alto * 0.39)
    x2, y2 = int(ancho * 0.79), int(alto * 0.57)

    zona_puntos = imagen[y1:y2, x1:x2]

    # Zona inferior de confirmación "ELIMINADO:"
    x3, y3 = int(ancho * 0.36), int(alto * 0.86)
    x4, y4 = int(ancho * 0.62), int(alto * 0.98)

    zona_confirmacion = imagen[y3:y4, x3:x4]

    eventos = []

    for nombre, zona in [
        ("puntos", zona_puntos),
        ("confirmacion", zona_confirmacion),
    ]:
        resultados = get_reader().readtext(zona, detail=0)

        for texto in resultados:
            texto_limpio = texto.lower().strip()

            if "elimin" in texto_limpio:
                eventos.append({
                    "zona": nombre,
                    "texto": texto,
                })

    return eventos


def main():
    if len(sys.argv) < 2:
        print(
            'Uso: python -m backend.kill_detector '
            '"data/frames/imagen.jpg"'
        )
        return

    ruta = Path(sys.argv[1])

    if not ruta.exists():
        print(f"No existe el archivo: {ruta}")
        return

    print(f"Analizando: {ruta.name}")

    eventos = detectar_kills(ruta)

    if eventos:
        print("¡POSIBLE KILL DETECTADA!")

        for evento in eventos:
            print(
                f"Zona: {evento['zona']} | "
                f"Texto: {evento['texto']}"
            )
    else:
        print("No se detectaron eliminaciones en esta imagen.")


if __name__ == "__main__":
    main()
