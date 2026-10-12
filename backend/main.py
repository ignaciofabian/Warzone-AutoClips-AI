
from pathlib import Path
import subprocess

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from backend.processor import create_reel

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "clips" / "raw"
PROCESSED_DIR = BASE_DIR / "clips" / "processed"


app = FastAPI(
    title="Warzone AutoClips AI",
    version="0.1.0"
)

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "clips" / "raw"
PROCESSED = ROOT / "clips" / "processed"


@app.get("/")
def home():
    return {
        "project": "Warzone AutoClips AI",
        "status": "online"
    }


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/clips")
def list_clips():
    return {
        "raw": [p.name for p in RAW.iterdir() if p.is_file()],
        "processed": [
            p.name for p in PROCESSED.iterdir()
            if p.is_file()
        ]
    }

class ProcessClipRequest(BaseModel):
    filename: str


@app.post("/clips/process")
def process_clip(request: ProcessClipRequest):
    # Solo permitimos nombres de archivo, no rutas externas
    filename = request.filename

    if (
        not filename
        or filename in (".", "..")
        or Path(filename).name != filename
        or "/" in filename
        or "\\" in filename
    ):
        raise HTTPException(
            status_code=400,
            detail="Nombre de archivo no válido"
        )

    input_path = RAW_DIR / filename

    if not input_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="El clip no existe"
        )

    try:
        output_path = create_reel(input_path)
    except subprocess.CalledProcessError:
        raise HTTPException(
            status_code=500,
            detail="Error al procesar el video con FFmpeg"
        )

    return {
        "message": "Reel generado correctamente",
        "input": filename,
        "output": output_path.name
    }
