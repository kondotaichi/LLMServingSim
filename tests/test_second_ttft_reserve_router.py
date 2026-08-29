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


class DynamicFormulaRouterTest(unittest.TestCase):
    @staticmethod
    def formula_prediction():
        return {
            'route_probability': 1.0,
            'route_positive_ms': 10.0,
            'route_upper_ms': 10.0,
            'route_ms': 10.0,
            'scheduler_ms': 1.0,
            'compute_ms': 1.0,
            'ttft_ms': 12.0,
        }

    @staticmethod
    def add_formula_features(request):
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

    @staticmethod
    def constrain(scheduler):
        scheduler.request.append(FakeRequest(99 + scheduler.instance_id, 100, 10_000))
        scheduler.memory.mem_for_kv = 100_500

    def make_router(self, schedulers, policy):
        router = Router(
            len(schedulers), schedulers, 1,
            routing_policy=policy,
            gpu_backbone_bandwidth_gbps=100.0,
            apn_fixed_propagation_ns=10.0,
            oneshot_redirect_margin_ns=0,
            oneshot_max_local_wait_ns=1,
        )
        router.ttft_formula = SimpleNamespace(
            predict=lambda _features, _policy: self.formula_prediction()
        )
        return router

    def test_blocked_target_remains_undecided_then_redirects(self):
        home = make_scheduler(0)
        target = make_scheduler(1)
        self.constrain(home)
        self.constrain(target)
        router = self.make_router(
            [home, target], 'NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE'
        )
        request = make_request(1)
        self.add_formula_features(request)

        deferred = router._maybe_capacity_dynamic_formula_route(request, 1_000)

        self.assertTrue(deferred)
        self.assertNotIn('_oneshot_selected_route', request)
        self.assertEqual(
            request['geo']['oneshot_decision_reason'],
            'awaiting_candidate_capacity',
        )

        target.request.clear()
        deferred = router._maybe_capacity_dynamic_formula_route(request, 2_000)

        self.assertTrue(deferred)
        self.assertEqual(request['_oneshot_selected_route'], 'kv_handoff')
        self.assertEqual(request['assigned_instance_id'], 1)

    def test_multi_candidate_uses_available_non_second_target(self):
        home = make_scheduler(0)
        second = make_scheduler(1)
        third = make_scheduler(2)
        self.constrain(home)
        self.constrain(second)
        router = self.make_router(
            [home, second, third],
            'NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE',
        )
        request = make_request(1)
        self.add_formula_features(request)

        deferred = router._maybe_capacity_dynamic_formula_route(request, 1_000)

        self.assertTrue(deferred)
        self.assertEqual(request['_oneshot_selected_route'], 'kv_handoff')
        self.assertEqual(request['assigned_instance_id'], 2)
        self.assertEqual(
            request['geo']['oneshot_selected_target_instance_id'], 2
        )

    def test_counterfactual_override_forces_admissible_candidate(self):
        home = make_scheduler(0)
        first = make_scheduler(1)
        forced = make_scheduler(2)
        self.constrain(home)
        router = Router(
            3, [home, first, forced], 1,
            routing_policy='NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE',
            gpu_backbone_bandwidth_gbps=100.0,
            apn_fixed_propagation_ns=10.0,
            oneshot_redirect_margin_ns=0,
            oneshot_max_local_wait_ns=1,
            counterfactual_request_id=1,
            counterfactual_target_instance_id=2,
        )
        router.ttft_formula = SimpleNamespace(
            predict=lambda _features, _policy: self.formula_prediction()
        )
        request = make_request(1)
        self.add_formula_features(request)

        router._maybe_capacity_dynamic_formula_route(request, 1_000)

        self.assertEqual(request['assigned_instance_id'], 2)
        selected = [
            row for row in router.candidate_diagnostics
            if row['selected_for_routing'] == 1
        ]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]['candidate_instance_id'], 2)
        self.assertEqual(selected[0]['counterfactual_override'], 1)

    def test_multi_waiting_selects_target_with_fewest_waiting_requests(self):
        home = make_scheduler(0)
        busy = make_scheduler(1, requests=[FakeRequest(10, 100, 120)])
        idle = make_scheduler(2)
        self.constrain(home)
        router = self.make_router(
            [home, busy, idle],
            'NEAREST_CAPACITY_MULTI_WAITING_FORMULA_KV_RESERVE',
        )
        request = make_request(1)
        self.add_formula_features(request)

        router._maybe_capacity_dynamic_formula_route(request, 1_000)

        self.assertEqual(request['assigned_instance_id'], 2)
        self.assertEqual(
            request['geo']['oneshot_candidate_selector'], 'min_waiting'
        )

    def test_multi_pressure_selects_target_with_lowest_capacity_pressure(self):
        home = make_scheduler(0)
        pressured = make_scheduler(
            1, requests=[FakeRequest(10, 100, 50_000)]
        )
        idle = make_scheduler(2)
        self.constrain(home)
        router = self.make_router(
            [home, pressured, idle],
            'NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE',
        )
        request = make_request(1)
        self.add_formula_features(request)

        router._maybe_capacity_dynamic_formula_route(request, 1_000)

        self.assertEqual(request['assigned_instance_id'], 2)
        self.assertEqual(
            request['geo']['oneshot_candidate_selector'], 'min_pressure'
        )

    def test_multi_random_uses_seeded_candidate_choice(self):
        home = make_scheduler(0)
        targets = [make_scheduler(instance_id) for instance_id in (1, 2, 3)]
        self.constrain(home)
        router = self.make_router(
            [home, *targets],
            'NEAREST_CAPACITY_MULTI_RANDOM_FORMULA_KV_RESERVE',
        )
        request = make_request(1)
        self.add_formula_features(request)

        router._maybe_capacity_dynamic_formula_route(request, 1_000)

        self.assertEqual(request['assigned_instance_id'], 3)
        self.assertEqual(
            request['geo']['oneshot_candidate_selector'], 'random'
        )

    def test_multi_pressure_no_model_redirects_without_formula_features(self):
        home = make_scheduler(0)
        pressured = make_scheduler(
            1, requests=[FakeRequest(10, 100, 50_000)]
        )
        idle = make_scheduler(2)
        self.constrain(home)
        router = Router(
            3, [home, pressured, idle], 1,
            routing_policy='NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE',
            gpu_backbone_bandwidth_gbps=100.0,
            apn_fixed_propagation_ns=10.0,
        )
        request = make_request(1)

        deferred = router._maybe_capacity_dynamic_formula_route(
            request, 1_000
        )

        self.assertTrue(deferred)
        self.assertIsNone(router.ttft_formula)
        self.assertEqual(request['assigned_instance_id'], 2)
        self.assertEqual(
            request['geo']['oneshot_candidate_selector'],
            'min_pressure_no_model',
        )
        self.assertEqual(
            request['geo']['oneshot_decision_reason'],
            'home_not_admissible_min_pressure',
        )

    def test_multi_pressure_cold_control_recomputes_redirected_prefix(self):
        home = make_scheduler(0)
        target = make_scheduler(1)
        self.constrain(home)
        router = Router(
            2, [home, target], 1,
            routing_policy='NEAREST_CAPACITY_MULTI_PRESSURE_COLD_RESERVE',
            gpu_backbone_bandwidth_gbps=100.0,
            apn_fixed_propagation_ns=10.0,
        )
        request = make_request(1)

        deferred = router._maybe_capacity_dynamic_formula_route(request, 1_000)

        self.assertTrue(deferred)
        self.assertEqual(request['assigned_instance_id'], 1)
        self.assertEqual(request['geo']['oneshot_selected_route'], 'cold_migrate')
        self.assertNotIn('failover', request)


class FakeMigrationMemory(FakeMemory):
    """Extends FakeMemory with just enough of seed_migrated_prefix's
    contract for _apply_kv_migration_if_needed, without any of the real
    prefix-cache/eviction machinery."""

    @staticmethod
    def seed_migrated_prefix(token_ids, prefix_len):
        return min(int(prefix_len), len(token_ids or []))


def make_migrate_kv_req_data(arrival_time_ns=1_000, reuse_prefix_toks=50,
                              speculative_elapsed_ns=None):
    req_data = {
        'arrival_time_ns': arrival_time_ns,
        'input_toks': 100,
        'input_hash_ids': list(range(100)),
        'failover': {
            'failover_mode': 'migrate_kv',
            'reuse_prefix_toks': reuse_prefix_toks,
            'kv_migration_bandwidth_gbps': 100.0,
            'kv_migration_distance_m': 10.0,
            'distance_latency_ns_per_meter': 5.0,
        },
        'geo': {},
    }
    if speculative_elapsed_ns is not None:
        req_data['_speculative_kv_elapsed_ns'] = speculative_elapsed_ns
    return req_data


class SchedulerHideKvMigrationTest(unittest.TestCase):
    """_apply_kv_migration_if_needed: Method A (overlap) and Method B
    (speculative-transfer credit) at the router level, independent of the
    scheduler.py batch_req gate (covered separately in
    tests/test_scheduler_hide_kv_migration.py)."""

    def make_router(self, **overrides):
        return Router(
            1, [make_scheduler(0)], 1,
            routing_policy='NEAREST_MIGRATE_KV',
            gpu_backbone_bandwidth_gbps=100.0,
            gpu_backbone_distance_m=10.0,
            **overrides,
        )

    def test_default_behavior_keeps_kv_migration_serial(self):
        router = self.make_router()
        target = make_scheduler(0)
        target.memory = FakeMigrationMemory()
        req_data = make_migrate_kv_req_data()

        router._apply_kv_migration_if_needed(req_data, target)

        migration_ns = req_data['failover']['kv_migration_latency_ns']
        self.assertGreater(migration_ns, 0)
        self.assertEqual(req_data['arrival_time_ns'], 1_000 + migration_ns)
        self.assertEqual(req_data['geo']['kv_ready_time_ns'], req_data['arrival_time_ns'])
        self.assertEqual(
            req_data['failover']['kv_migration_effective_latency_ns'], migration_ns
        )
        self.assertEqual(req_data['failover']['kv_migration_speculative_hidden_ns'], 0)

    def test_scheduler_hide_flag_splits_arrival_from_kv_ready(self):
        router = self.make_router(enable_scheduler_hide_kv_migration=True)
        target = make_scheduler(0)
        target.memory = FakeMigrationMemory()
        req_data = make_migrate_kv_req_data()

        router._apply_kv_migration_if_needed(req_data, target)

        migration_ns = req_data['failover']['kv_migration_latency_ns']
        self.assertGreater(migration_ns, 0)
        # Method A: request is visible to the target scheduler immediately.
        self.assertEqual(req_data['arrival_time_ns'], 1_000)
        # ...but prefill compute must still wait for the full transfer.
        self.assertEqual(req_data['geo']['kv_ready_time_ns'], 1_000 + migration_ns)
        self.assertEqual(
            req_data['failover']['kv_migration_effective_latency_ns'], migration_ns
        )

    def test_speculative_credit_shortens_effective_migration(self):
        router = self.make_router(
            enable_scheduler_hide_kv_migration=True,
            enable_speculative_kv_migration=True,
        )
        target = make_scheduler(0)
        target.memory = FakeMigrationMemory()
        req_data = make_migrate_kv_req_data(speculative_elapsed_ns=10)

        router._apply_kv_migration_if_needed(req_data, target)

        migration_ns = req_data['failover']['kv_migration_latency_ns']
        self.assertEqual(
            req_data['failover']['kv_migration_effective_latency_ns'], migration_ns - 10
        )
        self.assertEqual(
            req_data['failover']['kv_migration_speculative_hidden_ns'], 10
        )
        self.assertEqual(req_data['geo']['kv_ready_time_ns'], 1_000 + migration_ns - 10)

    def test_speculative_credit_clamps_to_full_migration(self):
        router = self.make_router(
            enable_scheduler_hide_kv_migration=True,
            enable_speculative_kv_migration=True,
        )
        target = make_scheduler(0)
        target.memory = FakeMigrationMemory()
        # Elapsed speculative time far exceeds what migration would ever
        # cost -- effective latency must clamp at 0, never go negative.
        req_data = make_migrate_kv_req_data(speculative_elapsed_ns=10**12)

        router._apply_kv_migration_if_needed(req_data, target)

        self.assertEqual(req_data['failover']['kv_migration_effective_latency_ns'], 0)
        self.assertEqual(req_data['geo']['kv_ready_time_ns'], req_data['arrival_time_ns'])


class SpeculativeKvPinAndRecalibrationTest(unittest.TestCase):
    """_maybe_capacity_dynamic_formula_route: B-1 local-wait recalibration
    and Method B pin/commit/waste bookkeeping."""

    INFLATED_PREDICTION = {
        'route_probability': 1.0, 'route_positive_ms': 10.0,
        # A residual this large mirrors the production formula's fixed
        # ~90th-percentile safety margin, which structurally always exceeds
        # any reasonable oneshot_max_local_wait_ns.
        'route_upper_ms': 5_010.0, 'route_ms': 10.0,
        'scheduler_ms': 1.0, 'compute_ms': 1.0, 'ttft_ms': 12.0,
    }

    @staticmethod
    def add_formula_features(request):
        request['_ttft_formula_workload_features'] = {
            'request_rate_rps': 3.0, 'arrival_offset_s': 10.0,
            'interarrival_ms': 300.0, 'global_arrivals_1s': 2,
            'global_arrivals_5s': 15, 'home_arrivals_1s': 1,
            'home_arrivals_5s': 2, 'home_workload_share': 0.1,
        }

    @staticmethod
    def constrain(scheduler):
        scheduler.request.append(FakeRequest(99 + scheduler.instance_id, 100, 10_000))
        scheduler.memory.mem_for_kv = 100_500

    def make_router(self, schedulers, margin_ns, max_wait_ns, **overrides):
        router = Router(
            len(schedulers), schedulers, 1,
            routing_policy='NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE',
            gpu_backbone_bandwidth_gbps=100.0,
            apn_fixed_propagation_ns=10.0,
            oneshot_redirect_margin_ns=margin_ns,
            oneshot_max_local_wait_ns=max_wait_ns,
            **overrides,
        )
        router.ttft_formula = SimpleNamespace(
            predict=lambda _features, _policy: self.INFLATED_PREDICTION
        )
        return router

    def test_inflated_upper_bound_is_default(self):
        home = make_scheduler(0)
        target = make_scheduler(1)
        self.constrain(home)
        router = self.make_router([home, target], margin_ns=0, max_wait_ns=10**9)
        request = make_request(1)
        self.add_formula_features(request)

        router._maybe_capacity_dynamic_formula_route(request, 1_000)

        self.assertEqual(
            request['geo']['oneshot_predicted_local_wait_ns'],
            self.INFLATED_PREDICTION['route_upper_ms'] * 1e6,
        )

    def test_point_estimate_flag_uses_plain_ttft(self):
        home = make_scheduler(0)
        target = make_scheduler(1)
        self.constrain(home)
        router = self.make_router(
            [home, target], margin_ns=0, max_wait_ns=10**9,
            enable_formula_local_wait_point_estimate=True,
        )
        request = make_request(1)
        self.add_formula_features(request)

        router._maybe_capacity_dynamic_formula_route(request, 1_000)

        self.assertEqual(
            request['geo']['oneshot_predicted_local_wait_ns'],
            self.INFLATED_PREDICTION['ttft_ms'] * 1e6,
        )

    def test_speculative_pin_credits_elapsed_time_on_matching_commit(self):
        home = make_scheduler(0)
        target = make_scheduler(1)
        self.constrain(home)
        # margin=1e6ns: redirect (~12.0003ms) is not enough better than
        # local (12ms point estimate) to win outright -> defers on call 1.
        # max_wait=1e9ns (1s): local_wait (12ms) alone never trips the
        # deadline; only accumulated elapsed_wait will, on call 2.
        router = self.make_router(
            [home, target], margin_ns=10**6, max_wait_ns=10**9,
            enable_formula_local_wait_point_estimate=True,
            enable_scheduler_hide_kv_migration=True,
            enable_speculative_kv_migration=True,
        )
        request = make_request(1)
        self.add_formula_features(request)

        deferred = router._maybe_capacity_dynamic_formula_route(request, 1_000)
        self.assertTrue(deferred)
        self.assertEqual(
            request['geo']['oneshot_decision_reason'], 'awaiting_predicted_local'
        )
        pinned_target = request['_speculative_kv_target_instance_id']
        self.assertEqual(pinned_target, 1)
        pin_time_ns = request['_speculative_kv_pin_time_ns']
        self.assertEqual(pin_time_ns, 1_000)

        later = 1_000 + 2 * 10**9  # elapsed_wait now exceeds max_wait_ns
        deferred = router._maybe_capacity_dynamic_formula_route(request, later)

        # True here means "just committed, needs re-insertion into the
        # pending queue" -- matching the existing convention (e.g.
        # test_multi_candidate_uses_available_non_second_target), not
        # "still waiting". A *third* call would return False (already
        # decided, cold_migrate/kv_handoff short-circuit at the top).
        self.assertTrue(deferred)
        self.assertEqual(
            request['geo']['oneshot_decision_reason'], 'elapsed_local_wait_exceeds_limit'
        )
        self.assertEqual(request['_oneshot_selected_route'], 'kv_handoff')
        self.assertEqual(request['assigned_instance_id'], pinned_target)
        self.assertEqual(
            request['_speculative_kv_elapsed_ns'], later - pin_time_ns
        )
        self.assertEqual(request['geo']['speculative_kv_wasted'], 0)

    def test_speculative_pin_marked_wasted_when_home_wins(self):
        home = make_scheduler(0)
        target = make_scheduler(1)
        self.constrain(home)
        router = self.make_router(
            [home, target], margin_ns=10**6, max_wait_ns=10**9,
            enable_formula_local_wait_point_estimate=True,
            enable_scheduler_hide_kv_migration=True,
            enable_speculative_kv_migration=True,
        )
        request = make_request(1)
        self.add_formula_features(request)

        deferred = router._maybe_capacity_dynamic_formula_route(request, 1_000)
        self.assertTrue(deferred)
        self.assertIn('_speculative_kv_target_instance_id', request)

        # Home frees up before the deadline fires.
        home.request.clear()
        deferred = router._maybe_capacity_dynamic_formula_route(request, 2_000)

        self.assertFalse(deferred)
        self.assertEqual(request['_oneshot_selected_route'], 'local')
        self.assertEqual(request['geo']['speculative_kv_wasted'], 1)
        self.assertGreaterEqual(request['geo']['speculative_kv_wasted_ns'], 0)


if __name__ == '__main__':
    unittest.main()
