
from pathlib import Path
from fastapi import FastAPI

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
