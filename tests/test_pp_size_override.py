import copy
import unittest

from serving.core.config_builder import _apply_pp_size_override


class PpSizeOverrideTest(unittest.TestCase):
    def test_omitted_override_preserves_config(self):
        nodes = [{"instances": [{"num_npus": 4, "tp_size": 2, "pp_size": 2}]}]
        original = copy.deepcopy(nodes)

        _apply_pp_size_override(nodes, None)

        self.assertEqual(nodes, original)

    def test_override_preserves_tp_and_changes_npus(self):
        nodes = [{"instances": [{"tp_size": 1}]} for _ in range(5)]

        _apply_pp_size_override(nodes, 2)

        for node in nodes:
            instance = node["instances"][0]
            self.assertEqual(instance["tp_size"], 1)
            self.assertEqual(instance["pp_size"], 2)
            self.assertEqual(instance["num_npus"], 2)

    def test_override_infers_tp_from_existing_parallelism(self):
        nodes = [{"instances": [{"num_npus": 4, "pp_size": 2}]}]

        _apply_pp_size_override(nodes, 3)

        self.assertEqual(nodes[0]["instances"][0]["tp_size"], 2)
        self.assertEqual(nodes[0]["instances"][0]["pp_size"], 3)
        self.assertEqual(nodes[0]["instances"][0]["num_npus"], 6)

    def test_rejects_non_positive_override(self):
        with self.assertRaisesRegex(ValueError, "must be >= 1"):
            _apply_pp_size_override([], 0)


if __name__ == "__main__":
    unittest.main()
