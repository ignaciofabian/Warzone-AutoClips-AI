import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

from backend.live_detector import Journal, Session, run_live, run_simulation
from backend.live_events import ConfirmationGate, HighlightTrigger
from backend.session_highlights import export_session, load_records, map_highlights


class SessionTests(unittest.TestCase):
    def test_live_capture_loop_with_synthetic_pixels_and_obs_worker(self):
        import itertools
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            session = Session(journal, ConfirmationGate(), HighlightTrigger(postroll=0), False)
            screen = MagicMock()
            screen.monitors = [{}, {"left": 0, "top": 0, "width": 1920, "height": 1080}]
            pixels = np.zeros((130, 499, 4), dtype=np.uint8)
            screen.grab.side_effect = [pixels] * 6 + [KeyboardInterrupt()]
            capture = MagicMock()
            capture.__enter__.return_value = screen
            texts = [f"ELIMINADO: {name}" for name in ["Alpha", "Alpha", "Beta", "Beta", "Gamma", "Gamma"]]
            controller = Mock()
            controller.save.return_value = str(Path(directory) / "replay.mkv")
            controller.requested_at = 3
            args = SimpleNamespace(threads=1, dry_run=False, monitor=1,
                                   roi=(0.36, 0.86, 0.62, 0.98), confidence=0.65, interval=0.5)
            with patch("mss.mss", return_value=capture), patch(
                    "backend.kill_detector.get_reader"), patch(
                    "backend.kill_detector.leer_confirmacion", side_effect=texts), patch(
                    "backend.obs_controller.ReplayController", return_value=controller), patch(
                    "backend.live_detector.time.monotonic", side_effect=itertools.count(0, 0.1)), patch(
                    "backend.live_detector.time.sleep"):
                with self.assertRaises(KeyboardInterrupt):
                    run_live(args, session)
            journal.close()
            records = load_records(journal.path)
            self.assertEqual(sum(r["kind"] == "kill" for r in records), 3)
            self.assertEqual(sum(r["kind"] == "replay_saved" for r in records), 1)
            controller.connect.assert_called_once()
            controller.save.assert_called_once()
            controller.close.assert_called_once()
            self.assertEqual(screen.grab.call_args.args[0]["width"], 499)

    def test_fixture_to_journal_without_obs(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            session = Session(journal, ConfirmationGate(), HighlightTrigger(), True)
            args = SimpleNamespace(simulate=Path(__file__).parent / "fixtures" / "observations.json")
            run_simulation(args, session)
            journal.close()
            records = load_records(journal.path)
            self.assertEqual(sum(r["kind"] == "kill" for r in records), 4)
            requests = [r for r in records if r["kind"] == "replay_request"]
            self.assertEqual(len(requests), 1)
            self.assertTrue(requests[0]["dry_run"])
            self.assertEqual(requests[0]["segundo"], 14.5)

    def test_duration_mapping_warm_buffer_and_clamping(self):
        kills = [{"tipo": "kill", "id": str(t), "segundo": t} for t in [10, 101, 105, 109]]
        covered, highlights = map_highlights(kills, saved_second=112, duration=20)
        self.assertEqual([k["segundo"] for k in covered], [9, 13, 17])
        self.assertEqual(highlights[0]["inicio_clip"], 1)
        self.assertEqual(highlights[0]["fin_clip"], 20)

    def test_overlap_does_not_export_same_kills_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "source.mkv"
            video.write_bytes(b"fixture")
            records = [{"kind": "kill", "tipo": "kill", "id": str(t), "segundo": t}
                       for t in [10, 11, 12]]
            records += [{"kind": "replay_saved", "path": str(video), "segundo": t}
                        for t in [20, 25]]
            session = root / "session.jsonl"
            session.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
            with patch("backend.session_highlights.ROOT", root), patch(
                    "backend.session_highlights.video_duration", return_value=30):
                exported = export_session(session)
            self.assertEqual(len(exported), 1)
            highlights = json.loads((root / "data/highlights/session_replay_001.json").read_text())
            self.assertEqual(highlights[0]["cantidad_kills"], 3)
            self.assertEqual(exported[0].read_bytes(), b"fixture")

    def test_truncated_final_journal_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.jsonl"
            path.write_text('{"kind": "kill"}\n{"kind":', encoding="utf-8")
            self.assertEqual(load_records(path), [{"kind": "kill"}])


class OBSTests(unittest.TestCase):
    def setUp(self):
        from backend.obs_controller import ReplayController
        self.controller_type = ReplayController

    def test_confirmed_save_and_persistent_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "replay.mkv"
            path.write_bytes(b"fixture")
            client = Mock()
            client.get_replay_buffer_status.return_value.output_active = True
            client.get_last_replay_buffer_replay.side_effect = [
                SimpleNamespace(saved_replay_path=""), SimpleNamespace(saved_replay_path=str(path)),
                SimpleNamespace(saved_replay_path="old"), SimpleNamespace(saved_replay_path=str(path))]
            factory = Mock(return_value=client)
            controller = self.controller_type(factory)
            self.assertEqual(controller.save(), str(path.resolve()))
            self.assertEqual(controller.save(), str(path.resolve()))
            factory.assert_called_once()
            self.assertEqual(client.save_replay_buffer.call_count, 2)
            controller.close()
            client.disconnect.assert_called_once()

    def test_disabled_buffer_no_save(self):
        client = Mock()
        client.get_replay_buffer_status.return_value.output_active = False
        controller = self.controller_type(lambda: client)
        with self.assertRaises(RuntimeError):
            controller.save()
        client.save_replay_buffer.assert_not_called()
        client.disconnect.assert_called_once()

    def test_timeout_and_error_never_resend(self):
        client = Mock()
        client.get_replay_buffer_status.return_value.output_active = True
        client.get_last_replay_buffer_replay.return_value.saved_replay_path = "unchanged"
        controller = self.controller_type(lambda: client, timeout=0)
        with self.assertRaises(TimeoutError):
            controller.save()
        client.save_replay_buffer.assert_called_once()
        client.disconnect.assert_called_once()
        client.reset_mock()
        client.save_replay_buffer.side_effect = ConnectionError("secret should not be logged")
        with self.assertRaises(ConnectionError):
            controller.save()
        client.save_replay_buffer.assert_called_once()


class OCRTests(unittest.TestCase):
    def test_image_roi_downscale_confidence_and_split_boxes(self):
        import numpy as np
        from backend.kill_detector import leer_confirmacion
        image = np.zeros((120, 1280, 3), dtype=np.uint8)
        reader = Mock()
        reader.readtext.return_value = [([], "ELIMINADO:", 0.99), ([], "Alpha", 0.8),
                                       ([], "noise", 0.1)]
        with patch("backend.kill_detector.get_reader", return_value=reader):
            self.assertEqual(leer_confirmacion(image), "ELIMINADO: Alpha")
        self.assertEqual(reader.readtext.call_args.args[0].shape, (60, 640, 3))
        self.assertEqual(reader.readtext.call_args.kwargs["workers"], 0)


if __name__ == "__main__":
    unittest.main()
