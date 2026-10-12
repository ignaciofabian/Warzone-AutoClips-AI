
import json
import re
import sys
from pathlib import Path

from backend.kill_detector import detectar_kills


FRAMES_DIR = Path("data/frames")
EVENTS_DIR = Path("data/events")


def extraer_segundos(nombre_archivo):
    """
    Ejemplo:
    Replay 2026-10-11 22-48-41_00104000.jpg
    corresponde al segundo 104.
    """
    coincidencia = re.search(r"_(\d{8})\.jpg$", nombre_archivo)

    if not coincidencia:
        return None

    milisegundos = int(coincidencia.group(1))
    return milisegundos / 1000


def analizar_video(nombre_video):
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)

    nombre_base = Path(nombre_video).stem

    imagenes = sorted(
        FRAMES_DIR.glob(f"{nombre_base}_*.jpg")
    )

    if not imagenes:
        print("No se encontraron fotogramas para este video.")
        return

    print(f"Analizando {len(imagenes)} fotogramas...")

    eventos = []
    ultimo_evento = {}

    for imagen in imagenes:
        segundo = extraer_segundos(imagen.name)

        if segundo is None:
            continue

        detecciones = detectar_kills(imagen)

        for deteccion in detecciones:
            if deteccion["zona"] != "confirmacion":
                continue

            texto = deteccion["texto"]

            if ":" not in texto:
                continue

            enemigo = texto.split(":", 1)[1].strip()

            if not enemigo:
                continue

            # Evita contar la misma eliminación
            # repetida en fotogramas cercanos.
            clave = enemigo.lower()
            anterior = ultimo_evento.get(clave)

            if anterior is not None and segundo - anterior < 8:
                continue

            evento = {
                "tipo": "kill",
                "segundo": segundo,
                "enemigo": enemigo,
                "texto": texto,
                "imagen": imagen.name,
            }

            eventos.append(evento)
            ultimo_evento[clave] = segundo

            print(
                f"KILL DETECTADA | "
                f"{segundo:.1f}s | {enemigo}"
            )

    salida = EVENTS_DIR / f"{nombre_base}.json"

    with salida.open("w", encoding="utf-8") as archivo:
        json.dump(eventos, archivo, indent=4, ensure_ascii=False)

    print(f"\nTotal de eventos detectados: {len(eventos)}")
    print(f"Resultados guardados en: {salida}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            'Uso: python -m backend.event_detector '
            '"Replay 2026-10-11 22-48-41.mkv"'
        )
        sys.exit(1)

    analizar_video(sys.argv[1])
