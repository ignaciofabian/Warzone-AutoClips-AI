
from pathlib import Path
import sys

import cv2
import easyocr


# Inicializamos OCR una sola vez.
# CPU evita problemas de compatibilidad CUDA al comenzar.
reader = easyocr.Reader(["es", "en"], gpu=False)


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
        resultados = reader.readtext(zona, detail=0)

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
