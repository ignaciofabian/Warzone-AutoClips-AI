
import os
import time
from pathlib import Path
import obsws_python as obs
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def conectar_obs():
    if not os.getenv("OBS_PASSWORD"):
        raise RuntimeError("Configura OBS_PASSWORD en el entorno o .env")
    cliente = obs.ReqClient(
        host=os.getenv("OBS_HOST", "127.0.0.1"),
        port=int(os.getenv("OBS_PORT", "4455")),
        password=os.environ["OBS_PASSWORD"],
        timeout=5,
    )
    return cliente


def guardar_replay(cliente=None):
    propio = cliente is None
    cliente = cliente or conectar_obs()
    try:
        estado = cliente.get_replay_buffer_status()
        if not estado.output_active:
            print("El Replay Buffer está apagado. Inícialo en OBS.")
            return False
        cliente.save_replay_buffer()
        print("Orden de guardado enviada a OBS.")
        return True
    finally:
        if propio:
            cliente.disconnect()


class ReplayController:
    """Una conexión reutilizada; verificar archivo nuevo, sin reenvío automático."""
    def __init__(self, factory=conectar_obs, timeout=10):
        self.factory = factory
        self.timeout = timeout
        self.client = None
        self.requested_at = None

    def connect(self):
        if self.client is None:
            self.client = self.factory()
        if not self.client.get_replay_buffer_status().output_active:
            raise RuntimeError("Inicia el Replay Buffer en OBS")

    def save(self):
        try:
            self.connect()
            previous = self.client.get_last_replay_buffer_replay().saved_replay_path
            self.requested_at = time.monotonic()
            self.client.save_replay_buffer()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                path = self.client.get_last_replay_buffer_replay().saved_replay_path
                if path and path != previous and Path(path).is_file():
                    return str(Path(path).resolve())
                time.sleep(0.2)
            raise TimeoutError("OBS no confirmó un archivo nuevo; no se reenvía la orden")
        except Exception:
            self.close()
            raise

    def close(self):
        client, self.client = self.client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        guardar_replay()
    except Exception as error:
        # Nunca mostrar argumentos del SDK ni valores de autenticación.
        print(f"Error al guardar replay ({type(error).__name__}). Revisa OBS y .env.")
