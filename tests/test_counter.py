import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch
from types import SimpleNamespace

from backend.counter_detector import CounterGate, read_counter
from backend.counter_calibration import load_calibration, write_phase
from backend.live_detector import Journal, Session, run_live
from backend.live_events import ConfirmationGate, HighlightTrigger


class CounterTests(unittest.TestCase):
    def test_five_minutes_without_kills_keeps_accumulated_count(self):
        gate = self.gate()
        gate.observe(1, .99, 1)
        gate.observe(1, .99, 1.5)
        for tick in range(4, 604):
            self.assertFalse(gate.observe(1, .99, tick / 2))
        self.assertFalse(gate.observe(2, .99, 302))
        events = gate.observe(2, .99, 302.5)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["counter_index"], 2)
        self.assertEqual(events[0]["time_bounds"], {"start": 301.5, "end": 302})
        self.assertFalse(gate.last_decision["reference_expired"])

    def test_two_digit_boundaries_and_one_to_eleven_rejected(self):
        for previous, value in [(9, 10), (10, 11), (19, 20)]:
            gate = CounterGate()
            gate.set_phase("match")
            gate.observe(previous, .99, 0)
            gate.observe(previous, .99, .5)
            gate.observe(value, .99, 1)
            events = gate.observe(value, .99, 1.5)
            self.assertEqual([e["counter_index"] for e in events], [value])
        gate = self.gate()
        gate.observe(1, .99, 1)
        gate.observe(1, .99, 1.5)
        for second in [2, 2.5]:
            self.assertFalse(gate.observe(11, .99, second))
            self.assertEqual(gate.last_decision["reason"], "implausible_change")
        self.assertEqual(gate.baseline, 1)

    def test_jump_records_shared_uncertainty_not_precise_individual_times(self):
        gate = self.gate()
        gate.observe(3, .99, 1)
        events = gate.observe(3, .99, 1.5)
        self.assertEqual(len(events), 3)
        self.assertTrue(all(e["time_bounds"] == {"start": .5, "end": 1} for e in events))
        self.assertTrue(all(not e["individual_time_observed"] for e in events))

    def gate(self):
        gate = CounterGate()
        gate.set_phase("match")
        gate.observe(0, .99, 0)
        gate.observe(0, .99, .5)
        return gate

    def test_increment_and_multikill_jump(self):
        gate = self.gate()
        self.assertEqual(gate.observe(1, .99, 1), [])
        self.assertEqual(len(gate.observe(1, .99, 1.5)), 1)
        self.assertEqual(gate.observe(4, .99, 2), [])
        events = gate.observe(4, .99, 2.5)
        self.assertEqual([e["counter_index"] for e in events], [2, 3, 4])
        self.assertEqual(gate.last_decision["difference"], 3)
        self.assertEqual(gate.observe(4, .99, 3), [])

    def test_flicker_confidence_wrong_numbers_and_no_abandonment_of_valid_baseline(self):
        gate = self.gate()
        self.assertFalse(gate.observe(1, .99, 1))
        self.assertFalse(gate.observe(0, .99, 1.5))
        for t, value, conf in [(2, 90, .99), (2.5, 90, .99), (3, 1, .2), (3.5, None, 0)]:
            self.assertFalse(gate.observe(value, conf, t))
        self.assertEqual(gate.baseline, 0)
        self.assertFalse(gate.observe(1, .99, 4))
        self.assertEqual(len(gate.observe(1, .99, 4.5)), 1)

    def test_initial_nonzero_is_baseline_not_historical_kills(self):
        gate = CounterGate()
        gate.set_phase("match")
        gate.observe(12, .99, 0)
        self.assertFalse(gate.observe(12, .99, .5))
        self.assertEqual(gate.last_decision["reason"], "baseline_established")

    def test_reset_needs_four_zero_readings_and_nonzero_decrease_rejected(self):
        gate = self.gate()
        gate.observe(3, .99, 1)
        gate.observe(3, .99, 1.5)
        old_epoch = gate.epoch
        self.assertFalse(gate.observe(2, .99, 2))
        self.assertEqual(gate.last_decision["reason"], "implausible_change")
        for t in [2.5, 3, 3.5]:
            self.assertFalse(gate.observe(0, .99, t))
            self.assertEqual(gate.baseline, 3)
        self.assertFalse(gate.observe(0, .99, 4))
        self.assertEqual(gate.baseline, 0)
        self.assertGreater(gate.epoch, old_epoch)
        gate.observe(1, .99, 4.5)
        self.assertEqual(len(gate.observe(1, .99, 5)), 1)

    def test_loading_timeout_rebaseline_without_creating_kills(self):
        gate = self.gate()
        self.assertFalse(gate.observe(None, 0, 1))
        self.assertFalse(gate.observe(None, 0, 4))
        self.assertIsNone(gate.baseline)
        gate.observe(3, .99, 4.5)
        self.assertFalse(gate.observe(3, .99, 5))
        self.assertEqual(gate.baseline, 3)

    def test_phase_changes_discard_warmup_kills(self):
        gate = CounterGate()
        for phase in ["unknown", "warmup", "loading"]:
            gate.set_phase(phase)
            self.assertFalse(gate.observe(6, .99, 0))
        gate.set_phase("match")
        gate.observe(0, .99, .5)
        gate.observe(0, .99, 1)
        gate.observe(1, .99, 1.5)
        self.assertEqual(len(gate.observe(1, .99, 2)), 1)

    def test_sample_gap_requires_fresh_confirmation(self):
        gate = self.gate()
        gate.observe(1, .99, 1)
        self.assertFalse(gate.observe(1, .99, 3))
        self.assertEqual(len(gate.observe(1, .99, 3.5)), 1)

    def test_numeric_ocr_skips_detector_and_does_not_join_other_numbers(self):
        import numpy as np
        reader = Mock()
        reader.recognize.return_value = [([], "12", .97)]
        with patch("backend.kill_detector.get_reader", return_value=reader):
            reading = read_counter(np.zeros((40, 70, 3), dtype=np.uint8))
            self.assertEqual(reading["value"], 12)
            self.assertEqual(reader.recognize.call_args.kwargs["allowlist"], "0123456789")
            reader.readtext.assert_not_called()
            reader.recognize.return_value = [([], "1", .99), ([], "2", .99)]
            self.assertIsNone(read_counter(np.zeros((40, 70, 3), dtype=np.uint8))["value"])


class FusionTests(unittest.TestCase):
    def test_uncertain_minutes_cannot_trigger_multikill_live_or_offline(self):
        from backend.highlight_detector import detectar_highlights
        events = [{"tipo": "kill", "segundo": 300, "counter_epoch": 1,
                   "time_bounds": {"start": 0, "end": 300}} for _ in range(3)]
        trigger = HighlightTrigger()
        for event in events:
            self.assertIsNone(trigger.add(event))
        self.assertFalse(detectar_highlights(events))

    def test_video_mapping_translates_uncertainty_and_tracks_ids(self):
        from backend.session_highlights import map_highlights
        events = [{"tipo": "kill", "id": str(i), "segundo": 105.5, "counter_epoch": 1,
                   "time_bounds": {"start": 104, "end": 105}} for i in range(3)]
        covered, highlights = map_highlights(events, saved_second=110, duration=20)
        self.assertEqual(covered[0]["time_bounds"], {"start": 14, "end": 15})
        self.assertEqual(highlights[0]["inicio"], 14)
        self.assertEqual(highlights[0]["fin"], 15)
        self.assertEqual(highlights[0]["event_ids"], ["0", "1", "2"])

    def test_live_counter_primary_uses_calibration_and_rate_limits_secondary(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            gate = CounterGate()
            gate.set_phase("match")
            session = Session(journal, ConfirmationGate(), HighlightTrigger(postroll=0), True, counter=gate)
            screen = MagicMock()
            screen.monitors = [{}, {}, {"left": 0, "top": 0, "width": 2560, "height": 1440}]
            template = Path(__file__).parent / "fixtures" / "skull-real.png"
            pixels = cv2.imread(str(template))
            screen.grab.return_value = np.concatenate((pixels, np.full((*pixels.shape[:2], 1), 255, dtype=np.uint8)), axis=2)
            context = MagicMock()
            context.__enter__.return_value = screen
            args = SimpleNamespace(threads=1, dry_run=True, debug_ocr=False, monitor=2,
                                   roi=(.32, .85, .60, .96), confidence=.65, interval=.5,
                                   phase="match", phase_file=Path(directory) / "phase.json",
                                   secondary_interval=1, ocr_max_width=640,
                                   skull_confidence=None, skull_confirmations=2,
                                   counter_preprocess="otsu",
                                   counter_calibration={"version": 2, "monitor": 2, "resolution": [2560, 1440],
                                                        "roi": [.9, .05, .92, .08], "skull_roi": [.9, .02, .92, .04],
                                                        "skull_template_path": str(template), "skull_confidence": .8})
            readings = [{"value": v, "confidence": .99} for v in [0, 0, 1, 1, 4, 4]]
            with patch("mss.mss", return_value=context), patch("backend.kill_detector.get_reader"), patch(
                    "backend.counter_detector.read_counter", side_effect=readings + [KeyboardInterrupt()]), patch(
                    "backend.kill_detector.leer_confirmacion", return_value="ELIMINADO: Alpha") as secondary, patch(
                    "backend.live_detector.time.sleep"), patch("backend.obs_controller.ReplayController") as controller:
                with self.assertRaises(KeyboardInterrupt):
                    run_live(args, session)
            journal.close()
            rows = [json.loads(l) for l in journal.path.read_text().splitlines()]
            self.assertEqual(sum(r["kind"] == "kill" for r in rows), 4)
            self.assertEqual(sum(r["kind"] == "replay_request" for r in rows), 1)
            self.assertEqual(secondary.call_count, 1)
            controller.return_value.connect.assert_not_called()
            controller.return_value.save.assert_not_called()

    def test_session_disarms_after_reset_and_keeps_replay_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            gate = CounterGate()
            gate.set_phase("match")
            trigger = HighlightTrigger()
            trigger.last_request = 1
            session = Session(journal, ConfirmationGate(), trigger, True, counter=gate)
            for t, v in [(0, 0), (.5, 0), (1, 3), (1.5, 3), (2, 0), (2.5, 0), (3, 0), (3.5, 0)]:
                session.observe_counter({"value": v, "confidence": .99}, t)
            self.assertEqual(gate.phase, "unknown")
            self.assertIsNone(trigger.pending)
            self.assertEqual(trigger.last_request, 1)
            self.assertFalse(session.observe_counter({"value": 1, "confidence": .99}, 4))
            journal.close()

    def test_offline_highlights_do_not_mix_rounds(self):
        from backend.highlight_detector import detectar_highlights
        events = [{"tipo": "kill", "segundo": t, "counter_epoch": epoch}
                  for t, epoch in [(0, 1), (1, 1), (2, 2)]]
        self.assertFalse(detectar_highlights(events))

    def test_visual_calibration_from_frozen_image_and_cancel(self):
        import cv2
        import numpy as np
        from backend.counter_calibration import calibrate
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "monitor.png"
            pixels = np.zeros((1440, 2560, 3), dtype=np.uint8)
            cv2.rectangle(pixels, (2408, 18), (2448, 40), (255, 255, 255), 2)
            cv2.imwrite(str(image), pixels)
            args = SimpleNamespace(output=root / "roi.json", image=image, monitor=2, delay=0)
            with patch("cv2.selectROI", side_effect=[(1200, 5, 30, 20), (1200, 30, 30, 20)]), patch(
                    "cv2.imshow"), patch("cv2.waitKey", return_value=13), patch("cv2.destroyAllWindows"):
                calibrate(args)
            config = load_calibration(args.output, 2)
            self.assertTrue((root / "roi-monitor.png").is_file())
            self.assertTrue((root / "roi-roi-zoom.png").is_file())
            self.assertEqual(config["resolution"], [2560, 1440])
            self.assertEqual(config["roi"], [2400 / 2560, 60 / 1440, 2460 / 2560, 100 / 1440])
            self.assertEqual(config["version"], 2)
            self.assertTrue((root / "roi-skull.png").is_file())
            self.assertTrue((root / "roi-skull-zoom.png").is_file())
            args.output = root / "cancel.json"
            with patch("cv2.selectROI", return_value=(0, 0, 0, 0)), patch("cv2.destroyAllWindows"):
                calibrate(args)
            self.assertFalse(args.output.exists())

    def test_secondary_before_and_after_primary_never_creates_duplicate_kill(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            gate = CounterGate()
            gate.set_phase("match")
            session = Session(journal, ConfirmationGate(), HighlightTrigger(), True, counter=gate)
            session.observe_counter({"value": 0, "confidence": .99}, 0)
            session.observe_counter({"value": 0, "confidence": .99}, .5)
            session.observe("ELIMINADO: Alpha", 1)
            session.observe("ELIMINADO: Alpha", 1.5)
            session.observe_counter({"value": 1, "confidence": .99}, 2)
            session.observe_counter({"value": 1, "confidence": .99}, 2.5)
            session.observe("ELIMINADO: Beta", 3)
            session.observe("ELIMINADO: Beta", 3.5)
            session.observe("ELIMINADO: Beta", 4)
            journal.close()
            rows = [json.loads(l) for l in journal.path.read_text().splitlines()]
            self.assertEqual(sum(r["kind"] == "kill" for r in rows), 1)
            secondary = [r for r in rows if r["kind"] == "secondary_evidence"]
            self.assertIsNone(secondary[0]["event_id"])
            self.assertIsNotNone(secondary[1]["event_id"])
            self.assertTrue(all(not r["counted_as_new_kill"] for r in secondary))

    def test_calibration_resolution_and_phase_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            path.write_text(json.dumps({"version": 1, "monitor": 2, "resolution": [2560, 1440],
                                        "roi": [.9, .05, .92, .08]}))
            self.assertEqual(load_calibration(path, 2)["monitor"], 2)
            with self.assertRaises(ValueError):
                load_calibration(path, 1)
            write_phase(path, "warmup")
            revision = json.loads(path.read_text())["revision"]
            write_phase(path, "match")
            self.assertNotEqual(json.loads(path.read_text())["revision"], revision)


@unittest.skipUnless(os.getenv("AUTOCLIPS_OCR_TEST") == "1", "OCR real optativo, requiere modelos locales")
class RealCounterOCRTests(unittest.TestCase):
    def test_real_zero_roi_with_otsu(self):
        import cv2
        from backend.kill_detector import get_reader
        image = cv2.imread(str(Path(__file__).parent / "fixtures/counter-zero-real.png"))
        self.assertIsNotNone(image)
        get_reader(download_enabled=False, threads=1)
        result = read_counter(image, preprocessing="otsu")
        self.assertEqual(result["value"], 0)
        self.assertGreaterEqual(result["confidence"], .85)


if __name__ == "__main__":
    unittest.main()
