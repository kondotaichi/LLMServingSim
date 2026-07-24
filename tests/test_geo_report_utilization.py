import unittest
from types import SimpleNamespace

from serving.core.geo_report import (
    _interval_union_ns,
    aggregate_gpu_utilization_timeseries,
)


class GpuUtilizationIntervalTest(unittest.TestCase):
    def test_merges_overlapping_intervals(self):
        intervals = [(10, 30), (20, 40), (50, 70)]
        self.assertEqual(_interval_union_ns(intervals, 0, 100), 50)

    def test_clips_to_observation_window(self):
        intervals = [(-10, 20), (80, 120), (130, 140)]
        self.assertEqual(_interval_union_ns(intervals, 0, 100), 40)

    def test_ignores_empty_intervals(self):
        intervals = [(10, 10), (20, 15)]
        self.assertEqual(_interval_union_ns(intervals, 0, 100), 0)

    def test_timeseries_reports_each_gpu_and_window(self):
        request = SimpleNamespace(request_send_time_ns=0, request_end_time_ns=20)
        schedulers = [
            SimpleNamespace(
                instance_id=0, done=[request],
                batch_busy_intervals_ns=[(0, 5), (8, 15)],
            ),
            SimpleNamespace(
                instance_id=1, done=[], batch_busy_intervals_ns=[(5, 20)],
            ),
        ]
        rows = aggregate_gpu_utilization_timeseries(schedulers, None, 10)
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]["utilization_pct"], 70.0)
        self.assertEqual(rows[1]["utilization_pct"], 50.0)
        self.assertEqual(rows[2]["utilization_pct"], 50.0)
        self.assertEqual(rows[3]["utilization_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
