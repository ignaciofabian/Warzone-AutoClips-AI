import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from backend.diagnostics import BannerGate, Diagnostics, FailureImages, prefix_match, recheck, summarize, timing_metrics
from backend.live_detector import Journal, Session, run_live
from backend.live_events import ConfirmationGate, HighlightTrigger


class StrategyTests(unittest.TestCase):
    def test_exact_prefix_without_enemy_or_colon(self):
        for text in ["ELIMINADO:", "ELIMINADO", "ELIMINADA Alpha", "ELIMINATED:"]:
            self.assertTrue(prefix_match(text))
        for text in ["NO ELIMINADO", "ASISTENCIA", "+100 ELIMINADO", "ELIMINACION", "ELIMINADOS"]:
            self.assertFalse(prefix_match(text, True))
        self.assertFalse(prefix_match("ELIMINAD0:"))
        self.assertTrue(prefix_match("ELIMINAD0:", True))

    def test_unstable_enemy_only_banner_strategy_accepts(self):
        strict, banner = ConfirmationGate(), BannerGate()
        strict.observe("ELIMINADO: Foxy", 0)
        banner.observe("ELIMINADO: Foxy", 0)
        self.assertIsNone(strict.observe("ELIMINADO: F0xy", 0.5))
        self.assertEqual(strict.last_decision["reason"], "name_changed")
        self.assertTrue(banner.observe("ELIMINADO: F0xy", 0.5))

    def test_different_names_on_continuous_banner_do_not_duplicate(self):
        banner = BannerGate()
        emitted = [banner.observe(f"ELIMINADO: {name}", i * 0.5)
                   for i, name in enumerate(["Alpha", "Alpha", "Beta", "Beta", "Gamma", "Gamma"])]
        self.assertEqual(sum(emitted), 1)
        # Episodios pegados se subcuentan deliberadamente; comparación, no producción.
        self.assertEqual(banner.last_decision["reason"], "persistent_banner")

    def test_persistent_banner_never_repeats_even_after_ttl(self):
        banner = BannerGate()
        self.assertEqual(sum(banner.observe("ELIMINADO:", i * 0.5) for i in range(100)), 1)

    def test_flash_and_sampling_gap_do_not_confirm(self):
        for gate in [ConfirmationGate(), BannerGate()]:
            self.assertFalse(gate.observe("ELIMINADO: Alpha", 0))
            self.assertFalse(gate.observe("", 0.5))
            self.assertFalse(gate.observe("ELIMINADO: Alpha", 1))
            self.assertFalse(gate.observe("ELIMINADO: Alpha", 3))
            self.assertEqual(gate.last_decision["reason"], "sample_gap")
            self.assertTrue(gate.observe("ELIMINADO: Alpha", 3.5))

    def test_absence_rearms_only_with_observed_samples_and_dedup(self):
        gate = BannerGate()
        gate.observe("ELIMINADO:", 0)
        self.assertTrue(gate.observe("ELIMINADO:", 0.5))
        for second in [1, 1.5, 2, 2.5, 3]:
            gate.observe("", second)
        self.assertFalse(gate.observe("ELIMINADO:", 3.5))
        self.assertEqual(gate.last_decision["reason"], "dedup_ttl")
        for second in [4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8]:
            gate.observe("", second)
        self.assertFalse(gate.observe("ELIMINADO:", 8.5))
        self.assertTrue(gate.observe("ELIMINADO:", 9))
        gate.observe("", 10)
        gate.observe("", 30)  # No suponer ausencia durante un hueco sin capturas.
        self.assertFalse(gate.observe("ELIMINADO:", 30.5))
        self.assertEqual(gate.last_decision["reason"], "persistent_banner")

    def test_configured_three_confirmations(self):
        for gate in [ConfirmationGate(confirmations=3), BannerGate(confirmations=3)]:
            self.assertFalse(gate.observe("ELIMINADO: Alpha", 0))
            self.assertFalse(gate.observe("ELIMINADO: Alpha", 0.5))
            self.assertTrue(gate.observe("ELIMINADO: Alpha", 1))

    def test_short_banner_two_real_observations_vs_one(self):
        slow, fast = BannerGate(), BannerGate()
        slow.observe("ELIMINADO:", 0)
        self.assertFalse(slow.observe("", .5))
        fast.observe("ELIMINADO:", 0)
        self.assertTrue(fast.observe("ELIMINADO:", .2))
        self.assertFalse(fast.observe("", .5))


class DiagnosticTests(unittest.TestCase):
    def test_live_diagnostic_capture_reads_once_per_frame_without_obs(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            diagnostic = Diagnostics(journal, confirmations=2, max_gap=1.5, dedup_seconds=8,
                                     interval=.5, save_images=True, image_limit=1)
            session = Session(journal, ConfirmationGate(), HighlightTrigger(), True, diagnostic)
            screen = MagicMock()
            screen.monitors = [{}, {}, {"left": 1920, "top": 0, "width": 1920, "height": 1080}]
            pixels = np.zeros((119, 538, 4), dtype=np.uint8)
            screen.grab.side_effect = [pixels] * 4 + [KeyboardInterrupt()]
            context = MagicMock()
            context.__enter__.return_value = screen
            reader = Mock()
            reader.readtext.side_effect = [[]] + [[([], "ELIMINADO:", .95), ([], "Foxy", .4)]] * 3
            controller = Mock()
            args = SimpleNamespace(dry_run=True, threads=1, confidence=.65, interval=.5,
                                   ocr_max_width=640, monitor=2, roi=(.32, .85, .60, .96))
            with patch("mss.mss", return_value=context), patch(
                    "backend.kill_detector.get_reader", return_value=reader), patch(
                    "backend.obs_controller.ReplayController", return_value=controller), patch(
                    "backend.live_detector.time.sleep"):
                with self.assertRaises(KeyboardInterrupt):
                    run_live(args, session)
            diagnostic.close()
            journal.close()
            controller.connect.assert_not_called()
            controller.save.assert_not_called()
            self.assertEqual(reader.readtext.call_count, 4)
            records = [json.loads(line) for line in journal.path.read_text().splitlines()]
            samples = [r for r in records if r["kind"] == "ocr_sample"]
            self.assertEqual(len(samples), 4)
            self.assertEqual(samples[-1]["strategies"]["strict_name"]["reason"], "invalid_prefix_colon_or_name")
            self.assertTrue(samples[-1]["filter_effect"]["removed_strict_candidate"])
            self.assertTrue(any(r["kind"] == "diagnostic_image" and r["written"] for r in records))
            self.assertEqual(summarize(journal.path)["accepted_by_strategy"]["prefix_exact"], 1)
            self.assertTrue(all(r["reading"]["ocr_seconds"] >= 0 for r in samples))

    def test_cli_diagnostics_forces_dry_run_and_preserves_defaults(self):
        from backend.live_detector import main
        with tempfile.TemporaryDirectory() as directory:
            with patch("sys.argv", ["live_detector", "--diagnostics", "--log-dir", directory]), patch(
                    "backend.live_detector.run_live") as live:
                self.assertEqual(main(), 0)
            args, session = live.call_args.args
            self.assertTrue(args.dry_run)
            self.assertTrue(session.dry_run)
            self.assertEqual(args.confirmations, 2)
            self.assertEqual(args.cooldown, 30)
            self.assertIsNotNone(session.diagnostics)

    def test_ocr_reader_always_cpu_and_no_download_in_live(self):
        from backend.kill_detector import get_reader
        torch, easyocr = Mock(), Mock()
        with patch.dict("sys.modules", {"torch": torch, "easyocr": easyocr}), patch(
                "backend.kill_detector.reader", None):
            get_reader(download_enabled=False, threads=1)
        torch.set_num_threads.assert_called_once_with(1)
        self.assertFalse(easyocr.Reader.call_args.kwargs["gpu"])
        self.assertFalse(easyocr.Reader.call_args.kwargs["download_enabled"])

    def test_timing_distinguishes_jitter_and_sampling_overruns(self):
        self.assertEqual(timing_metrics(None, .5)["estimated_missed_slots"], 0)
        self.assertEqual(timing_metrics(.501, .5)["estimated_missed_slots"], 0)
        self.assertEqual(timing_metrics(.75, .5)["estimated_missed_slots"], 0)
        self.assertEqual(timing_metrics(.75, .5)["late_seconds"], .25)
        self.assertEqual(timing_metrics(1.6, .5)["estimated_missed_slots"], 2)

    def test_detailed_ocr_keeps_rejected_confidences(self):
        import numpy as np
        from backend.kill_detector import leer_confirmacion
        reader = Mock()
        reader.readtext.return_value = [([[0, 0], [10, 0]], "ELIMINADO:", .95),
                                       ([], "Foxy", .4)]
        with patch("backend.kill_detector.get_reader", return_value=reader):
            reading = leer_confirmacion(np.zeros((80, 900, 3), dtype=np.uint8), detailed=True)
        self.assertEqual(reading["text"], "ELIMINADO:")
        self.assertEqual(reading["raw_text"], "ELIMINADO: Foxy")
        self.assertFalse(reading["boxes"][1]["accepted"])
        self.assertEqual(reading["boxes"][1]["confidence"], .4)
        self.assertGreaterEqual(reading["ocr_seconds"], 0)
        self.assertEqual(reading["ocr_shape"][1], 640)
        json.dumps(reading)
        reader.readtext.assert_called_once()  # Las estrategias no repiten OCR.

    def test_records_explanations_filtering_summary_and_offline_recheck(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            journal.write("session_start", max_gap=1.5, dedup=8)
            diagnostics = Diagnostics(journal, confirmations=2, max_gap=1.5, dedup_seconds=8, interval=.5)
            gate = ConfirmationGate()
            for second in [0, .75]:
                reading = {"text": "ELIMINADO:", "raw_text": "ELIMINADO: Foxy", "ocr_seconds": .7,
                           "boxes": [{"text": "ELIMINADO:", "confidence": .95, "accepted": True},
                                     {"text": "Foxy", "confidence": .4, "accepted": False}]}
                gate.observe(reading["text"], second)
                diagnostics.record(reading, second, gate.last_decision)
            diagnostics.close()
            journal.close()
            rows = [json.loads(line) for line in journal.path.read_text().splitlines()]
            self.assertTrue(rows[-1]["filter_effect"]["removed_strict_candidate"])
            summary = summarize(journal.path)
            self.assertEqual(summary["low_confidence_boxes"], 2)
            self.assertEqual(summary["estimated_missed_slots"], 0)
            self.assertEqual(summary["accepted_by_strategy"]["strict_name"], 0)
            self.assertEqual(summary["accepted_by_strategy"]["prefix_exact"], 1)
            report = recheck(journal.path, confidence=.35)
            self.assertEqual(report["accepted_seconds"]["strict_name"], [.75])
            self.assertEqual(recheck(journal.path, confirmations=3)["accepted_seconds"]["prefix_exact"], [])

    def test_failed_roi_writer_limits_and_result(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            journal = Journal(Path(directory))
            images = FailureImages(Path(directory) / "failures", interval=2, limit=2)
            frame = np.zeros((10, 20, 3), dtype=np.uint8)
            first = images.submit(frame, 0, 1)
            self.assertEqual(first["status"], "queued")
            self.assertEqual(images.submit(frame, 0, 2)["status"], "writer_busy")
            images.future.result()
            images.collect(journal)
            self.assertTrue(Path(first["path"]).is_file())
            self.assertEqual(images.submit(frame, .5, 3)["status"], "rate_limited")
            self.assertEqual(images.submit(frame, 2, 4)["status"], "queued")
            images.close(journal)
            self.assertEqual(images.submit(frame, 4, 5)["status"], "limit_reached")
            journal.close()


if __name__ == "__main__":
    unittest.main()
