"""Bounded scheduler state, lease safety, and concurrent signal accounting."""
import threading
import tempfile
import unittest
from pathlib import Path

import mind as M


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="mind-scheduler-")
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_concurrent_signal_updates_are_exact(self):
        threads = [
            threading.Thread(
                target=M._scheduler_note_signals,
                args=(self.root, 1),
            )
            for _ in range(40)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        state = M._read_scheduler_state(self.root)
        self.assertEqual(state["pending"], 40)

    def test_only_one_concurrent_claim_wins_the_lease(self):
        M._scheduler_note_signals(
            self.root, M.AUTO_DREAM_SIGNALS)
        barrier = threading.Barrier(2)
        tokens = []

        def claim():
            barrier.wait()
            tokens.append(M._scheduler_claim(self.root))

        threads = [threading.Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sum(token is not None for token in tokens), 1)

    def test_signals_arriving_during_lease_remain_pending(self):
        M._scheduler_note_signals(
            self.root, M.AUTO_DREAM_SIGNALS)
        token = M._scheduler_claim(self.root)
        self.assertIsNotNone(token)

        M._scheduler_note_signals(self.root, 3)
        self.assertTrue(M._scheduler_complete(self.root, token))

        state = M._read_scheduler_state(self.root)
        self.assertEqual(state["pending"], 3)
        self.assertIsNone(state["lease_token"])


if __name__ == "__main__":
    unittest.main()
