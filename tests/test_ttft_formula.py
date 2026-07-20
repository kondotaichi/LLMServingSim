import csv
import unittest
from collections import defaultdict
from pathlib import Path

from serving.core.ttft_formula import NUMERIC_FEATURES, OfflineTtftFormula


class OfflineTtftFormulaTest(unittest.TestCase):
    def test_exported_formula_matches_saved_worked_examples(self):
        repo_root = Path(__file__).resolve().parents[1]
        artifact_dir = (
            repo_root / 'experiments/2026-07-16_ttft_component_regression/'
            'analysis/ttft_formula'
        )
        predictor = OfflineTtftFormula(artifact_dir)
        features = defaultdict(dict)
        policies = {}
        with (artifact_dir / 'examples/route_logistic_contributions.csv').open(newline='') as file:
            for row in csv.DictReader(file):
                case = row['case']
                term = row['term']
                transformed = float(row['transformed_value'])
                if term in NUMERIC_FEATURES:
                    mean, scale = predictor.scaling[term]
                    features[case][term] = mean + transformed * scale
                elif term.startswith('policy_') and transformed == 1.0:
                    policies[case] = term[len('policy_'):]

        with (artifact_dir / 'examples/example_summary.csv').open(newline='') as file:
            expected = {
                row['case']: float(row['ttft_ms'])
                for row in csv.DictReader(file)
            }

        for case, expected_ttft_ms in expected.items():
            prediction = predictor.predict(features[case], policies[case])
            self.assertAlmostEqual(prediction['ttft_ms'], expected_ttft_ms, places=8)


if __name__ == '__main__':
    unittest.main()
