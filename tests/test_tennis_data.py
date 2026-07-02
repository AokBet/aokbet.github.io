import unittest

from scripts.update_tennis_data import competition_parts, completed_set, norm_name, surface_for


class TennisDataTests(unittest.TestCase):
    def test_grand_slam_sections_are_classified(self):
        self.assertEqual(competition_parts("wimbledon", "mens-singles"), ("ATP", "singles", "wimbledon", "Grand Slam"))
        self.assertEqual(competition_parts("wimbledon", "womens-doubles"), ("WTA", "doubles", "wimbledon", "Grand Slam"))

    def test_challenger_category_uses_tournament_slug(self):
        self.assertEqual(competition_parts("atp-challenger", "brasov-romania")[:3], ("ATP", "singles", "brasov-romania"))

    def test_surface_mapping(self):
        self.assertEqual(surface_for("wimbledon"), "Grass")
        self.assertEqual(surface_for("brasov-romania"), "Clay")
        self.assertEqual(surface_for("unknown-event"), "Unknown")

    def test_set_completion(self):
        self.assertTrue(completed_set(6, 4))
        self.assertTrue(completed_set(7, 6))
        self.assertFalse(completed_set(5, 4))

    def test_player_name_normalization(self):
        self.assertEqual(norm_name("Félix Auger-Aliassime"), "felix auger aliassime")


if __name__ == "__main__":
    unittest.main()
