"""Automatic dispatch must preserve model and read-only boundaries."""
import pathlib
import runpy
import unittest

MOD = runpy.run_path(str(pathlib.Path(__file__).resolve().parents[1] / 'bin' / 'openswarm-auto'))
preference = MOD['preference']

class FleetAutoTests(unittest.TestCase):
    def test_task_profiles_cover_all_five_backends(self):
        expected = [('mechanical', 1, 'M1'), ('test', 2, 'K1'), ('impl', 2, 'GH1'),
                    ('impl', 3, 'C1'), ('impl', 4, 'GH2'), ('migration', 3, 'A1')]
        for kind, difficulty, lane in expected:
            self.assertEqual(preference(kind, difficulty)[0], lane)

    def test_complex_copilot_never_falls_back_to_haiku(self):
        for kind in ('impl', 'test', 'migration'):
            for difficulty in (4, 5):
                self.assertNotIn('GH1', preference(kind, difficulty))
        self.assertNotIn('GH2', preference('impl', 2))

if __name__ == '__main__': unittest.main()
