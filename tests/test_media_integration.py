"""Prueba real optativa de FFmpeg con video y audio sintéticos, sin OBS."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.getenv("AUTOCLIPS_MEDIA_TEST") == "1", "FFmpeg optativo")
class MediaIntegrationTests(unittest.TestCase):
    def test_horizontal_vertical_and_audio(self):
        import imageio_ffmpeg
        from backend import clip_generator, processor
        from backend.session_highlights import video_duration

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, metadata = root / "raw", root / "metadata"
            raw.mkdir()
            metadata.mkdir()
            video = raw / "synthetic.mp4"
            subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                            "color=c=blue:s=320x180:r=10:d=2", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=2", "-c:v", "libx264",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video)],
                           check=True, capture_output=True)
            (metadata / "synthetic.json").write_text(
                json.dumps([{"inicio_clip": 0, "fin_clip": 1}]), encoding="utf-8")
            with patch.object(clip_generator, "RAW_DIR", raw), patch.object(
                    clip_generator, "HIGHLIGHTS_DIR", metadata), patch.object(
                    clip_generator, "OUTPUT_DIR", root / "horizontal"):
                clip_generator.generar_clips(video.name)
            horizontal = root / "horizontal/synthetic_highlight_1.mp4"
            with patch.object(processor, "PROCESSED_DIR", root / "vertical"):
                vertical = processor.create_reel(horizontal)
            for path, resolution in [(horizontal, "320x180"), (vertical, "1080x1920")]:
                info = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path)],
                                      capture_output=True, text=True, errors="replace")
                self.assertIn(resolution, info.stderr)
                self.assertIn("Audio: aac", info.stderr)
                self.assertAlmostEqual(video_duration(path), 1, delta=0.15)


if __name__ == "__main__":
    unittest.main()
