import argparse
import unittest

from backend.highlight_detector import detectar_highlights
from backend.live_events import ConfirmationGate, HighlightTrigger, parse_confirmation
from backend.live_detector import parse_roi, roi_bounds


def kill(second, enemy="enemy"):
    return {"tipo": "kill", "segundo": second, "enemigo": enemy}


class ConfirmationTests(unittest.TestCase):
    def test_requires_elimination_confirmation(self):
        for text in ["+100 XP ELIMINACIÓN", "ABATIDO: Alpha", "ASISTENCIA: Alpha",
                     "ELIMINADO:", "feed ELIMINADO: Alpha", "ELIMINADO: A"]:
            self.assertIsNone(parse_confirmation(text))
        self.assertEqual(parse_confirmation("ELIMINÁDO: Alpha#123"), "alpha#123")
        self.assertEqual(parse_confirmation("ELIMINATED: Alpha"), "alpha")

    def test_isolated_reading_and_missing_sample(self):
        gate = ConfirmationGate()
        self.assertIsNone(gate.observe("ELIMINADO: Alpha", 0))
        self.assertIsNone(gate.observe("", 0.5))
        self.assertIsNone(gate.observe("ELIMINADO: Alpha", 1))
        self.assertIsNone(gate.observe("ELIMINADO: Alpha", 4))
        self.assertIsNotNone(gate.observe("ELIMINADO: Alpha", 4.5))

    def test_persistent_banner_does_not_repeat(self):
        gate = ConfirmationGate()
        found = [gate.observe("ELIMINADO: Alpha", i * 0.5) for i in range(100)]
        self.assertEqual(sum(e is not None for e in found), 1)

    def test_dedup_and_rearm(self):
        gate = ConfirmationGate()
        gate.observe("ELIMINADO: Alpha", 0)
        self.assertIsNotNone(gate.observe("ELIMINADO: Alpha", 0.5))
        gate.observe("", 1)
        self.assertIsNone(gate.observe("ELIMINADO: Alpha", 4))
        self.assertIsNone(gate.observe("ELIMINADO: Alpha", 4.5))
        gate.observe("", 5)
        self.assertIsNone(gate.observe("ELIMINADO: Alpha", 10))
        self.assertIsNotNone(gate.observe("ELIMINADO: Alpha", 10.5))

    def test_distinct_enemies_and_monotonic_time(self):
        gate = ConfirmationGate()
        for i, name in enumerate(["Alpha", "Beta", "Gamma"]):
            self.assertIsNone(gate.observe(f"ELIMINADO: {name}", i * 2))
            self.assertIsNotNone(gate.observe(f"ELIMINADO: {name}", i * 2 + 0.5))
        with self.assertRaises(ValueError):
            gate.observe("", 0)


class TriggerTests(unittest.TestCase):
    def test_window_postroll_and_cooldown(self):
        trigger = HighlightTrigger()
        self.assertIsNone(trigger.add(kill(0)))
        self.assertIsNone(trigger.add(kill(10)))
        self.assertIsNotNone(trigger.add(kill(15)))
        self.assertIsNone(trigger.take_due(19.9))
        self.assertIsNotNone(trigger.take_due(20))
        for t in [21, 22, 23]:
            self.assertIsNone(trigger.add(kill(t)))
        self.assertIsNone(trigger.take_due(40))
        for t in [50, 51]:
            self.assertIsNone(trigger.add(kill(t)))
        self.assertIsNotNone(trigger.add(kill(52)))

    def test_sliding_window_both_modes(self):
        trigger = HighlightTrigger(postroll=0)
        events = [kill(t) for t in [0, 14, 16, 20]]
        for event in events[:-1]:
            self.assertIsNone(trigger.add(event))
        self.assertIsNotNone(trigger.add(events[-1]))
        groups = detectar_highlights(events)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["inicio"], 14)

    def test_nonoverlapping_groups_preserved(self):
        groups = detectar_highlights([kill(t) for t in [0, 1, 2, 30, 31, 32]])
        self.assertEqual([g["cantidad_kills"] for g in groups], [3, 3])


class CaptureTests(unittest.TestCase):
    def test_roi_and_second_monitor_offset(self):
        monitor = {"left": -1920, "top": 0, "width": 1920, "height": 1080}
        bounds = roi_bounds(monitor, parse_roi("0.25,0.5,0.75,1"))
        self.assertEqual(bounds, {"left": -1440, "top": 540, "width": 960, "height": 540})
        for invalid in ["nan,0,1,1", "0,0,0,1", "0,0,2,1", "0,1"]:
            with self.assertRaises(argparse.ArgumentTypeError):
                parse_roi(invalid)


if __name__ == "__main__":
    unittest.main()
