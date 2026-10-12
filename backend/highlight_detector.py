
import json
import sys
from pathlib import Path

EVENTS_DIR = Path("data/events")
HIGHLIGHTS_DIR = Path("data/highlights")

# Tres eliminaciones en un intervalo máximo de 15 segundos.
MULTIKILL_WINDOW = 15
MIN_KILLS = 3


def event_bounds(event):
    bounds = event.get("time_bounds")
    return (bounds["start"], bounds["end"]) if bounds else (event["segundo"], event["segundo"])


def within_window(events):
    return max(event_bounds(e)[1] for e in events) - min(event_bounds(e)[0] for e in events) <= MULTIKILL_WINDOW


def detectar_highlights(eventos):
    kills = sorted(
        [
            evento
            for evento in eventos
            if evento.get("tipo") == "kill"
            and isinstance(evento.get("segundo"), (int, float))
        ],
        key=lambda evento: evento["segundo"],
    )

    highlights = []
    grupo = []

    for kill in kills:
        if grupo and kill.get("counter_epoch") != grupo[-1].get("counter_epoch"):
            if len(grupo) >= MIN_KILLS:
                highlights.append(crear_highlight(grupo))
            grupo = [kill]
            continue
        if not grupo:
            grupo = [kill]
            continue

        # Ventana móvil: descartar solo kills antiguas si aún no hay multikill.
        if within_window(grupo + [kill]):
            grupo.append(kill)
        else:
            if len(grupo) >= MIN_KILLS:
                highlights.append(crear_highlight(grupo))
                grupo = [kill]
            else:
                grupo = [evento for evento in grupo if within_window([evento, kill])]
                grupo.append(kill)

    if len(grupo) >= MIN_KILLS and within_window(grupo):
        highlights.append(crear_highlight(grupo))

    return highlights


def crear_highlight(grupo):
    inicio = min(event_bounds(e)[0] for e in grupo)
    fin = max(event_bounds(e)[1] for e in grupo)

    return {
        "tipo": "multikill",
        "cantidad_kills": len(grupo),
        "inicio": inicio,
        "fin": fin,
        "inicio_clip": max(0, inicio - 8),
        "fin_clip": fin + 5,
        "enemigos": [
            kill.get("enemigo", "desconocido")
            for kill in grupo
        ],
        "event_ids": [kill["id"] for kill in grupo if "id" in kill],
        "estado": "pendiente_revision",
    }


def main():
    if len(sys.argv) < 2:
        print(
            'Uso: python -m backend.highlight_detector '
            '"Replay 2026-10-11 22-48-41.mkv"'
        )
        return

    nombre_base = Path(sys.argv[1]).stem
    ruta_eventos = EVENTS_DIR / f"{nombre_base}.json"

    if not ruta_eventos.exists():
        print(f"No existe el archivo: {ruta_eventos}")
        return

    with ruta_eventos.open("r", encoding="utf-8") as archivo:
        eventos = json.load(archivo)

    highlights = detectar_highlights(eventos)

    HIGHLIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    salida = HIGHLIGHTS_DIR / f"{nombre_base}.json"

    with salida.open("w", encoding="utf-8") as archivo:
        json.dump(
            highlights,
            archivo,
            ensure_ascii=False,
            indent=4,
        )

    print(f"Eventos analizados: {len(eventos)}")
    print(f"Multikills candidatas: {len(highlights)}")

    for highlight in highlights:
        print(
            f"MULTIKILL | {highlight['cantidad_kills']} kills | "
            f"{highlight['inicio']}s - {highlight['fin']}s"
        )
        print(
            f"Clip sugerido: "
            f"{highlight['inicio_clip']}s - "
            f"{highlight['fin_clip']}s"
        )

    print(f"Resultados guardados en: {salida}")


if __name__ == "__main__":
    main()
