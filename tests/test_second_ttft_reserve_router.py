import unittest
import sys
import types
from types import SimpleNamespace

memory_model = types.ModuleType('serving.core.memory_model')
memory_model.Device = SimpleNamespace(NPU='NPU')
sys.modules.setdefault('serving.core.memory_model', memory_model)

from serving.core.router import Router


class FakeMemory:
    block_size = 1
    enable_prefix_caching = True
    mem_for_kv = 1_000_000
    npu_mem = 1_000_000
    weight = 0
    npu_used = 0

    @staticmethod
    def get_kv(tokens):
        return int(tokens) * 10

    @staticmethod
    def avail_size(_device):
        return 1_000_000


class FakeRequest:
    def __init__(self, request_id, input_tokens, output_tokens, computed=0):
        self.id = request_id
        self.original_input = input_tokens
        self.output = output_tokens
        self.num_computed_tokens = computed

    def is_prefill(self):
        return self.num_computed_tokens < self.original_input


def make_scheduler(instance_id, max_num_seqs=8, requests=None):
    scheduler = SimpleNamespace(
        instance_id=instance_id,
        pd_type='prefill',
        request=list(requests or []),
        inflight=[],
        max_num_seqs=max_num_seqs,
        max_num_batched_tokens=100,
        memory=FakeMemory(),
        num_npus=1,
        enable_prefix_caching=True,
    )
    return scheduler


def make_request(request_id):
    return {
        'index': request_id,
        'input_toks': 100,
        'output_toks': 120,
        'arrival_time_ns': 1_000,
        'assigned_instance_id': 0,
        'reuse_prefix_toks': 50,
        'geo': {
            'gpu_id': 0,
            'distance_m': 10.0,
            'second_nearest_gpu_id': 1,
            'second_nearest_distance_m': 20.0,
            'distance_latency_ns_per_meter': 5.0,
            'network_throughput_mbps': 1_000.0,
            'request_payload_bytes': 100.0,
            'first_token_payload_bytes': 10.0,
            'uplink_latency_ns': 100,
        },
    }


class SecondTtftReserveRouterTest(unittest.TestCase):
    def make_router(self, home, target):
        return Router(
            2,
            [home, target],
            2,
            routing_policy='NEAREST_SECOND_TTFT_RESERVE',
            gpu_backbone_bandwidth_gbps=100.0,
            gpu_backbone_distance_m=10.0,
            adaptive_token_time_ns=100.0,
            adaptive_iteration_time_ns=1.0,
        )

    def test_keeps_request_local_when_local_prediction_is_fastest(self):
        router = self.make_router(make_scheduler(0), make_scheduler(1))
        request = make_request(1)

        deferred = router._maybe_adaptive_route(request, 1_000)

        self.assertFalse(deferred)
        self.assertEqual(request['geo']['adaptive_selected_route'], 'local')
        self.assertFalse(router._adaptive_reservations)

    def test_kv_handoff_reserves_target_and_blocks_duplicate_slot(self):
        long_request = FakeRequest(99, 100, 10_000)
        home = make_scheduler(0, max_num_seqs=1, requests=[long_request])
        target = make_scheduler(1, max_num_seqs=1)
        router = self.make_router(home, target)
        first = make_request(1)

        deferred = router._maybe_adaptive_route(first, 1_000)

        self.assertTrue(deferred)
        self.assertEqual(first['geo']['adaptive_selected_route'], 'kv_handoff')
        self.assertIn(1, router._adaptive_reservations[1])
        self.assertEqual(first['_adaptive_reserved_instance_id'], 1)

        second = make_request(2)
        router._maybe_adaptive_route(second, 1_000)
        self.assertEqual(second['geo']['adaptive_selected_route'], 'local')
        self.assertNotIn(2, router._adaptive_reservations[1])

        router._release_adaptive_reservation(first)
        self.assertFalse(router._adaptive_reservations)

    def test_uses_cold_migration_without_reusable_prefix(self):
        long_request = FakeRequest(99, 100, 10_000)
        home = make_scheduler(0, max_num_seqs=1, requests=[long_request])
        target = make_scheduler(1)
        router = self.make_router(home, target)
        request = make_request(1)
        request['reuse_prefix_toks'] = 0

        deferred = router._maybe_adaptive_route(request, 1_000)

        self.assertTrue(deferred)
        self.assertEqual(request['geo']['adaptive_selected_route'], 'cold_migrate')
        self.assertNotIn('failover', request)
        self.assertEqual(
            router._adaptive_reservations[1][1]['prefill_tokens'],
            request['input_toks'],
        )


class CapacityOneshotRouterTest(unittest.TestCase):
    def make_router(
            self, home, target, margin_ns, max_wait_ns,
            enable_target_reservation=True):
        return Router(
            2,
            [home, target],
            2,
            routing_policy='NEAREST_CAPACITY_ONESHOT_KV_RESERVE',
            gpu_backbone_bandwidth_gbps=100.0,
            gpu_backbone_distance_m=10.0,
            adaptive_token_time_ns=100.0,
            adaptive_iteration_time_ns=1.0,
            oneshot_redirect_margin_ns=margin_ns,
            oneshot_max_local_wait_ns=max_wait_ns,
            enable_oneshot_target_reservation=enable_target_reservation,
        )

    @staticmethod
    def make_memory_constrained_home():
        active = FakeRequest(99, 100, 10_000)
        home = make_scheduler(0, requests=[active])
        home.memory.mem_for_kv = 100_500
        return home

    def test_local_decision_is_committed_across_capacity_retries(self):
        home = self.make_memory_constrained_home()
        target = make_scheduler(1)
        router = self.make_router(home, target, margin_ns=10**12, max_wait_ns=10**12)
        request = make_request(1)

        deferred = router._maybe_capacity_oneshot_route(request, 1_000)
        self.assertTrue(deferred)
        self.assertEqual(request['_oneshot_selected_route'], 'local')
        decision_time = request['geo']['oneshot_decision_time_ns']

        home.request.clear()
        deferred = router._maybe_capacity_oneshot_route(request, 2_000)
        self.assertFalse(deferred)
        self.assertEqual(request['_oneshot_selected_route'], 'local')
        self.assertEqual(request['geo']['oneshot_decision_time_ns'], decision_time)
        self.assertEqual(request['geo'].get('rerouted', 0), 0)

    def test_deadline_is_an_arrival_time_decision_not_a_later_timeout(self):
        home = self.make_memory_constrained_home()
        target = make_scheduler(1)
        router = self.make_router(home, target, margin_ns=10**12, max_wait_ns=1)
        request = make_request(1)

        deferred = router._maybe_capacity_oneshot_route(request, 1_000)

        self.assertTrue(deferred)
        self.assertEqual(request['_oneshot_selected_route'], 'kv_handoff')
        self.assertEqual(
            request['geo']['oneshot_decision_reason'],
            'predicted_local_wait_exceeds_limit',
        )
        self.assertEqual(request['geo']['oneshot_decision_time_ns'], 1_000)
        self.assertIn(1, router._adaptive_reservations[1])

    def test_redirect_can_skip_atomic_target_reservation(self):
        home = self.make_memory_constrained_home()
        target = make_scheduler(1)
        router = self.make_router(
            home, target, margin_ns=10**12, max_wait_ns=1,
            enable_target_reservation=False,
        )
        request = make_request(1)

        deferred = router._maybe_capacity_oneshot_route(request, 1_000)

        self.assertTrue(deferred)
        self.assertEqual(request['_oneshot_selected_route'], 'kv_handoff')
        self.assertEqual(
            request['geo']['oneshot_target_reservation_enabled'], 0
        )
        self.assertNotIn('_adaptive_reserved_instance_id', request)
        self.assertNotIn(1, router._adaptive_reservations)

    def test_formula_policy_uses_offline_component_prediction(self):
        home = self.make_memory_constrained_home()
        target = make_scheduler(1)
        router = Router(
            2,
            [home, target],
            1,
            routing_policy='NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE',
            gpu_backbone_bandwidth_gbps=100.0,
            gpu_backbone_distance_m=10.0,
            oneshot_redirect_margin_ns=10**12,
            oneshot_max_local_wait_ns=10**12,
        )
        request = make_request(1)
        request['_ttft_formula_workload_features'] = {
            'request_rate_rps': 3.0,
            'arrival_offset_s': 10.0,
            'interarrival_ms': 300.0,
            'global_arrivals_1s': 2,
            'global_arrivals_5s': 15,
            'home_arrivals_1s': 1,
            'home_arrivals_5s': 2,
            'home_workload_share': 0.1,
        }

        deferred = router._maybe_capacity_oneshot_route(request, 1_000)

        self.assertTrue(deferred)
        self.assertEqual(
            request['geo']['oneshot_prediction_model'],
            'offline_ttft_formula',
        )
        self.assertIn('oneshot_formula_local_route_probability', request['geo'])
        self.assertIn('oneshot_formula_redirect_compute_ms', request['geo'])


if __name__ == '__main__':
    unittest.main()
