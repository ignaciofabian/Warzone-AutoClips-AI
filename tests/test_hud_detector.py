import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch, MagicMock
from types import SimpleNamespace

from backend.counter_detector import CounterGate
from backend.hud_detector import SkullMatcher
from backend.live_detector import Journal, Session, run_live
from backend.live_events import ConfirmationGate, HighlightTrigger


def icon():
    import cv2
    import numpy as np
    image = np.zeros((32, 32, 3), dtype=np.uint8)
    cv2.circle(image, (16, 12), 8, (255, 255, 255), 2)
    cv2.rectangle(image, (12, 20), (20, 26), (255, 255, 255), 2)
    return image


class HUDTests(unittest.TestCase):
    def test_real_static_skull_and_other_hud_symbol(self):
        import cv2
        fixtures = Path(__file__).parent / "fixtures"
        skull = cv2.imread(str(fixtures / "skull-real.png"))
        other = cv2.imread(str(fixtures / "hud-person-real.png"))
        matcher = SkullMatcher(skull)
        matcher.observe(skull)
        self.assertTrue(matcher.observe(skull)["visible"])
        self.assertFalse(matcher.observe(other)["visible"])
        for confidence in [0, 1.1, float("nan")]:
            with self.assertRaises(ValueError):
                SkullMatcher(skull, confidence=confidence)

    def test_session_hidden_hud_preserves_total_phase_and_cooldown(self):
        journal = Mock()
        gate = self.gate()
        trigger = HighlightTrigger()
        session = Session(journal, ConfirmationGate(), trigger, True, counter=gate)
        trigger.last_request = 5
        session.observe_counter({"value": None, "confidence": 0, "hud_visible": False}, 60)
        self.assertEqual(gate.baseline, 2)
        self.assertEqual(gate.phase, "match")
        self.assertEqual(trigger.last_request, 5)
        for t in [61, 61.5]:
            self.assertFalse(session.observe_counter({"value": 3, "confidence": .99, "hud_visible": True}, t))
        events = session.observe_counter({"value": 3, "confidence": .99, "hud_visible": True}, 62)
        self.assertEqual(len(events), 1)
        self.assertEqual(gate.baseline, 3)

    def gate(self):
        gate = CounterGate()
        gate.set_phase("match")
        gate.observe(2, .99, 0, hud_visible=True)
        gate.observe(2, .99, .5, hud_visible=True)
        return gate

    def test_icon_presence_confirmation_missing_blank_and_wrong_image(self):
        import numpy as np
        image = icon()
        matcher = SkullMatcher(image)
        self.assertFalse(matcher.observe(image)["visible"])
        self.assertTrue(matcher.observe(image)["visible"])
        self.assertFalse(matcher.observe(np.zeros_like(image))["visible"])
        self.assertFalse(matcher.observe(np.full_like(image, 255))["visible"])
        with self.assertRaises(ValueError):
            SkullMatcher(np.zeros_like(image))

    def test_hidden_hud_preserves_total_and_phase_after_minutes(self):
        gate = self.gate()
        for second in [1, 3, 60, 300]:
            self.assertFalse(gate.observe(None, 0, second, hud_visible=False))
            self.assertEqual(gate.baseline, 2)
            self.assertEqual(gate.phase, "match")
        for second in [300.5, 301]:
            self.assertFalse(gate.observe(3, .99, second, hud_visible=True))
        events = gate.observe(3, .99, 301.5, hud_visible=True)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["time_bounds"], {"start": .5, "end": 300.5})

    def test_same_count_resumes_without_kills_and_false_jump_rejected(self):
        gate = self.gate()
        gate.observe(None, 0, 1, hud_visible=False)
        for t in [1.5, 2]:
            self.assertFalse(gate.observe(12, .99, t, hud_visible=True))
        for t in [2.5, 3, 3.5]:
            self.assertFalse(gate.observe(2, .99, t, hud_visible=True))
        self.assertFalse(gate.hud_suspended)
        self.assertEqual(gate.baseline, 2)

    def test_warmup_and_loading_never_become_match_from_icon(self):
        for phase in ["unknown", "warmup", "loading"]:
            gate = CounterGate()
            gate.set_phase(phase)
            for t in [0, .5, 1, 1.5]:
                self.assertFalse(gate.observe(3, .99, t, hud_visible=True))
            self.assertEqual(gate.phase, phase)

    def test_recovery_interrupted_and_reset_zero_needs_four_reads(self):
        gate = self.gate()
        gate.observe(None, 0, 1, hud_visible=False)
        gate.observe(3, .99, 1.5, hud_visible=True)
        gate.observe(None, 0, 2, hud_visible=False)
        for t in [2.5, 3, 3.5]:
            self.assertFalse(gate.observe(0, .99, t, hud_visible=True))
            self.assertEqual(gate.baseline, 2)
        self.assertFalse(gate.observe(0, .99, 4, hud_visible=True))
        self.assertEqual(gate.last_decision["reason"], "counter_reset")

    def test_live_missing_icon_skips_number_ocr_without_resetting_phase(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "skull.png"
            cv2.imwrite(str(template), icon())
            journal = Journal(root)
            gate = CounterGate()
            gate.set_phase("match")
            session = Session(journal, ConfirmationGate(), HighlightTrigger(), True, counter=gate)
            screen = MagicMock()
            screen.monitors = [{}, {}, {"left": 0, "top": 0, "width": 2560, "height": 1440}]
            screen.grab.side_effect = [np.zeros((32, 32, 4), dtype=np.uint8)] * 3 + [KeyboardInterrupt()]
            context = MagicMock()
            context.__enter__.return_value = screen
            args = SimpleNamespace(threads=1, dry_run=True, monitor=2, debug_ocr=False,
                                   roi=(.32, .85, .60, .96), phase="match", phase_file=root / "phase.json",
                                   interval=.5, secondary_interval=1, skull_confidence=None, skull_confirmations=2,
                                   counter_calibration={"version": 2, "resolution": [2560, 1440],
                                       "roi": [.9, .05, .92, .08], "skull_roi": [.9, .02, .92, .04],
                                       "skull_template_path": str(template), "skull_confidence": .8})
            try:
                with patch("mss.mss", return_value=context), patch("backend.kill_detector.get_reader"), patch(
                        "backend.counter_detector.read_counter") as reader, patch(
                        "backend.kill_detector.leer_confirmacion") as lower, patch(
                        "backend.live_detector.time.sleep"), patch("backend.obs_controller.ReplayController") as obs:
                    with self.assertRaises(KeyboardInterrupt):
                        run_live(args, session)
            finally:
                journal.close()
            reader.assert_not_called()
            lower.assert_not_called()
            obs.return_value.connect.assert_not_called()
            self.assertEqual(gate.phase, "match")


if __name__ == "__main__":
    unittest.main()
