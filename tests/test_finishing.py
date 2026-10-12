"""Regresiones de abatimiento/remate: el abatimiento no aporta confirmaciones."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from backend.diagnostics import Diagnostics, combat_labels, rejection_details
from backend.live_detector import Journal, Session, main, roi_bounds
from backend.live_events import ConfirmationGate, HighlightTrigger


class FinishingTests(unittest.TestCase):
    def test_immediate_and_delayed_finish_confirm_independently(self):
        for delay in [0.5, 3, 8, 30]:
            with self.subTest(delay=delay):
                gate = ConfirmationGate()
                self.assertIsNone(gate.observe("ABATIDO: Alpha", 0))
                self.assertEqual(gate.emitted, {})
                self.assertEqual(gate.last_seen, {})
                self.assertIsNone(gate.observe("ELIMINADO: Alpha", delay))
                event = gate.observe("ELIMINADO: Alpha", delay + .5)
                self.assertEqual(event["enemigo"], "alpha")
                self.assertEqual(event["segundo"], delay + .5)

    def test_down_only_never_kill_in_primary_or_shadow(self):
        from backend.diagnostics import BannerGate
        for gate in [ConfirmationGate(), BannerGate(), BannerGate(tolerant=True)]:
            for second in range(20):
                self.assertFalse(gate.observe("ABATIDO: Alpha", second * .5))

    def test_down_is_not_first_confirmation_for_short_elimination(self):
        for delay in [.5, 8]:
            gate = ConfirmationGate()
            gate.observe("ABATIDO: Alpha", 0)
            self.assertIsNone(gate.observe("ELIMINADO: Alpha", delay))
            self.assertIsNone(gate.observe("", delay + .5))
            self.assertEqual(gate.last_decision["interrupted_count"], 1)
            self.assertEqual(gate.emitted, {})

    def test_elimination_without_down_same_result(self):
        gate = ConfirmationGate()
        self.assertIsNone(gate.observe("ELIMINADO: Alpha", 8))
        self.assertEqual(gate.observe("ELIMINADO: Alpha", 8.5)["enemigo"], "alpha")

    def test_rapid_distinct_eliminations_and_repeated_notices(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            session = Session(journal, ConfirmationGate(), HighlightTrigger(postroll=0), True)
            for index, name in enumerate(["Alpha", "Beta", "Gamma", "Delta", "Echo", "Foxy"]):
                for offset in [0, .5, 1]:
                    session.observe(f"ELIMINADO: {name}", index * 1.5 + offset)
            journal.close()
            rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
            self.assertEqual(sum(r["kind"] == "kill" for r in rows), 6)
            self.assertEqual(sum(r["kind"] == "replay_request" for r in rows), 1)


class FinishDiagnosticTests(unittest.TestCase):
    def test_debug_cli_is_read_only_and_defaults_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("sys.argv", ["live_detector", "--debug-ocr", "--save-combat-images",
                                    "--monitor", "2", "--log-dir", directory]), patch(
                    "backend.live_detector.run_live") as live, patch("builtins.print"):
                self.assertEqual(main(), 0)
            args, session = live.call_args.args
            self.assertTrue(args.diagnostics)
            self.assertTrue(args.dry_run)
            self.assertTrue(session.diagnostics.debug_ocr)
            self.assertTrue(session.diagnostics.save_combat_images)
            self.assertEqual(args.confirmations, 2)
            self.assertEqual(args.cooldown, 30)

    def test_only_combat_images_including_confirmed_events_and_raw_low_confidence(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            diag = Diagnostics(journal, confirmations=2, max_gap=1.5, dedup_seconds=8,
                               interval=.5, debug_ocr=True, save_combat_images=True)
            diag.images.close(journal)
            images = diag.images = Mock()
            images.submit.return_value = {"status": "queued"}
            region = roi_bounds({"left": 0, "top": 0, "width": 2560, "height": 1440},
                                (.32, .85, .60, .96))
            with patch("builtins.print") as output:
                diag.configure_capture(monitor=2, resolution=[2560, 1440], bounds=region,
                                       roi=(.32, .85, .60, .96))
                for second, text, raw, reason in [
                    (0, "", "ABATIDO: Alpha", "empty_or_low_confidence"),
                    (.5, "", "", "empty_or_low_confidence"),
                    (8, "ELIMINADO: Alpha", "ELIMINADO: Alpha", "waiting_confirmation"),
                    (8.5, "ELIMINADO: Alpha", "ELIMINADO: Alpha", "accepted"),
                    (9, "ELIMINADO: Alpha", "ELIMINADO: Alpha", "dedup_ttl"),
                ]:
                    diag.record({"text": text, "raw_text": raw, "ocr_seconds": .75,
                                 "boxes": [{"text": raw, "confidence": .4, "accepted": False}]},
                                second, {"reason": reason}, frame=SimpleNamespace())
                messages = [json.loads(call.args[0].split("] ", 1)[1]) for call in output.call_args_list
                            if call.args[0].startswith("[DEBUG-OCR]")]
            self.assertEqual(images.submit.call_count, 4)
            self.assertEqual(messages[-1]["capture_region"]["bounds"]["width"], 717)
            self.assertEqual(messages[-1]["capture_region"]["bounds"]["height"], 158)
            self.assertEqual(messages[-1]["boxes"][0]["confidence"], .4)
            self.assertEqual(messages[-1]["ocr_seconds"], .75)
            self.assertEqual(messages[-1]["decision"]["reason"], "dedup_ttl")
            self.assertEqual(messages[2]["combat_context"]["seconds_since_down_text"], 8)
            self.assertFalse(messages[2]["combat_context"]["same_enemy_verified"])
            diag.close()
            journal.close()

    def test_rejection_of_extra_text_explained_without_relaxing_parser(self):
        reading = {"text": "3k ELIMINADO: Alpha", "raw_text": "3k ELIMINADO: Alpha", "boxes": []}
        gate = ConfirmationGate()
        self.assertIsNone(gate.observe(reading["text"], 0))
        self.assertIn("text_before_elimination_prefix", rejection_details(reading, gate.last_decision))
        self.assertIn("down_is_not_a_kill", rejection_details(
            {"text": "ABATIDO: Alpha", "raw_text": "ABATIDO: Alpha", "boxes": []}, {}))
        self.assertEqual(combat_labels("ABATID0: Alpha"), ["down"])
        self.assertEqual(combat_labels("ELIMINADO: Alpha"), ["elimination"])


if __name__ == "__main__":
    unittest.main()
