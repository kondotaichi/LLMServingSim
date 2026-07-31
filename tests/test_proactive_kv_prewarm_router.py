import unittest
from types import SimpleNamespace

# NOTE: deliberately does NOT stub serving.core.memory_model (unlike
# test_second_ttft_reserve_router.py) -- these tests need the real
# RadixCache for genuine match_prefix semantics, and the real
# memory_model module imports cleanly standalone. Stubbing it here would
# poison sys.modules for whichever other test file pytest happens to
# import afterward in the same session (memory_model.calculate_sizes is
# real and needed by trace_generator.py).
from serving.core.router import Router
from serving.core.radix_tree import RadixCache


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


class FakeProactiveMemory(FakeMemory):
    """Method C's payoff (Hook 2) verifies residency via a real
    match_prefix call before crediting -- unlike FakeMigrationMemory in
    test_second_ttft_reserve_router.py (which only fakes the token-count
    contract), these tests need genuine RadixCache insert/match_prefix
    semantics, so npu_prefix_cache is a real RadixCache. Byte-budget
    bookkeeping stays fake/unbounded like FakeMemory."""

    def __init__(self):
        self.npu_prefix_cache = RadixCache(
            node_id=0, device='NPU', page_size=1, capacity=10**9, kv_size=1,
        )

    def seed_migrated_prefix(self, token_ids, prefix_len, mark_speculative=False):
        prefix_len = min(int(prefix_len), len(token_ids or []))
        if prefix_len <= 0:
            return 0
        self.npu_prefix_cache.insert(list(token_ids[:prefix_len]))
        if mark_speculative:
            # Mirrors the production fix in memory_model.py's
            # seed_migrated_prefix: backdate so this evicts before
            # genuinely-accessed content under the real RadixCache's LRU.
            result = self.npu_prefix_cache.match_prefix(token_ids[:prefix_len])
            if result.last_device_node is not None:
                result.last_device_node.last_access_time = 0.0
        return prefix_len


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
        memory=FakeProactiveMemory(),
        num_npus=1,
        enable_prefix_caching=True,
    )
    return scheduler


def make_router(schedulers, **overrides):
    overrides.setdefault('enable_proactive_kv_prewarm', True)
    return Router(
        len(schedulers), schedulers, len(schedulers),
        routing_policy='NEAREST_MIGRATE_KV',
        gpu_backbone_bandwidth_gbps=100.0,
        gpu_backbone_distance_m=10.0,
        **overrides,
    )


def push_pressure(sched, output_tokens):
    """Add one waiting FakeRequest whose output size drives
    capacity_pressure = output_tokens * 10 / FakeMemory.mem_for_kv."""
    sched.request.append(FakeRequest(9000 + len(sched.request), 1, output_tokens))


def seed_user_arrival(router, instance_id, user_id, arrival_ns, tokens):
    router._instance_user_history.setdefault(instance_id, {}).setdefault(
        user_id, __import__('collections').deque()
    ).append(arrival_ns)
    router._user_last_seen_content[user_id] = {
        'input_hash_ids': tokens,
        'reuse_prefix_toks': len(tokens),
        'home_instance_id': instance_id,
        'seen_at_ns': arrival_ns,
    }


def make_migrate_kv_req_data(user_id, tokens, arrival_time_ns=1_000):
    return {
        'arrival_time_ns': arrival_time_ns,
        'input_toks': len(tokens),
        'input_hash_ids': list(tokens),
        # Top-level, mirroring the real workload row shape (see
        # make_request() in test_second_ttft_reserve_router.py) --
        # _record_user_activity reads this directly, separately from
        # failover['reuse_prefix_toks'] which the real migrate_kv path uses.
        'reuse_prefix_toks': len(tokens),
        'failover': {
            'failover_mode': 'migrate_kv',
            'reuse_prefix_toks': len(tokens),
            'kv_migration_bandwidth_gbps': 100.0,
            'kv_migration_distance_m': 10.0,
            'distance_latency_ns_per_meter': 5.0,
        },
        'geo': {'user_id': user_id},
    }


class TriggerGatingTest(unittest.TestCase):
    def test_pressure_below_threshold_no_trigger(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        router = make_router([home, away])
        seed_user_arrival(router, 0, 'alice', 0, [1, 2, 3])

        router.maybe_proactive_kv_prewarm(1_000)

        self.assertEqual(router.proactive_migration_log, [])
        self.assertEqual(router._proactive_migrations, {})

    def test_pressure_above_threshold_triggers(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        push_pressure(home, 90_000)  # 900_000 / 1_000_000 = 0.9 > default 0.8
        router = make_router([home, away])
        seed_user_arrival(router, 0, 'alice', 0, [1, 2, 3])

        router.maybe_proactive_kv_prewarm(1_000)

        self.assertEqual(len(router.proactive_migration_log), 1)
        self.assertIn('alice', router._proactive_migrations)
        pending = router._proactive_migrations['alice']
        self.assertEqual(pending['source_instance_id'], 0)
        self.assertEqual(pending['target_instance_id'], 1)
        self.assertEqual(pending['seeded_tokens'], 3)

    def test_cooldown_prevents_immediate_retrigger_on_same_instance(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        push_pressure(home, 90_000)
        router = make_router([home, away], proactive_kv_prewarm_cooldown_ns=5_000.0,
                              proactive_kv_prewarm_eval_interval_ns=1.0)
        seed_user_arrival(router, 0, 'alice', 0, [1, 2, 3])

        router.maybe_proactive_kv_prewarm(1_000)
        self.assertEqual(len(router.proactive_migration_log), 1)

        # A second user becomes eligible, but the instance is still under
        # cooldown -- must not trigger again this soon.
        seed_user_arrival(router, 0, 'bob', 1_001, [4, 5])
        router.maybe_proactive_kv_prewarm(2_000)

        self.assertEqual(len(router.proactive_migration_log), 1)

    def test_eval_interval_throttles_global_scan(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        push_pressure(home, 90_000)
        router = make_router([home, away], proactive_kv_prewarm_eval_interval_ns=1_000_000.0)
        seed_user_arrival(router, 0, 'alice', 0, [1, 2, 3])

        router.maybe_proactive_kv_prewarm(0)
        self.assertEqual(len(router.proactive_migration_log), 1)

        # Well within the eval interval: the whole scan must be skipped,
        # even though a fresh instance/user would otherwise be eligible.
        push_pressure(away, 90_000)
        seed_user_arrival(router, 1, 'carol', 100, [6, 7])
        router.maybe_proactive_kv_prewarm(100)

        self.assertEqual(len(router.proactive_migration_log), 1)

    def test_disabled_flag_is_complete_noop(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        push_pressure(home, 90_000)
        router = make_router([home, away], enable_proactive_kv_prewarm=False)
        # _record_user_activity itself must refuse to populate history when
        # disabled -- exercise it directly instead of the seed_user_arrival
        # test helper (which writes state unconditionally and would make
        # this assertion meaningless).
        activity_req_data = make_migrate_kv_req_data('alice', [1, 2, 3])
        activity_req_data['assigned_instance_id'] = 0
        router._record_user_activity(activity_req_data, 1_000)

        req_data = make_migrate_kv_req_data('alice', [1, 2, 3])
        router.maybe_proactive_kv_prewarm(1_000)
        router._resolve_proactive_kv_prewarm(req_data, home)
        router._apply_kv_migration_if_needed(req_data, away)

        self.assertEqual(router.proactive_migration_log, [])
        self.assertEqual(router._proactive_migrations, {})
        self.assertEqual(router._instance_user_history, {})
        self.assertEqual(router._user_last_seen_content, {})
        self.assertNotIn('proactive_kv_prewarm_hit', req_data['geo'])
        self.assertNotIn('proactive_kv_prewarm_wasted', req_data['geo'])


class TopKSelectionTest(unittest.TestCase):
    def test_top_k_selects_highest_frequency_users(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        push_pressure(home, 90_000)
        router = make_router([home, away], proactive_kv_prewarm_top_k=2)
        for i in range(5):
            seed_user_arrival(router, 0, 'alice', i, [1])
        for i in range(3):
            seed_user_arrival(router, 0, 'bob', i, [2])
        seed_user_arrival(router, 0, 'carol', 0, [3])

        router.maybe_proactive_kv_prewarm(1_000)

        self.assertIn('alice', router._proactive_migrations)
        self.assertIn('bob', router._proactive_migrations)
        self.assertNotIn('carol', router._proactive_migrations)

    def test_tie_break_by_recency_then_user_id(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        push_pressure(home, 90_000)
        router = make_router([home, away], proactive_kv_prewarm_top_k=1)
        # Both users have 1 arrival each (tied count); "zed" arrived later.
        seed_user_arrival(router, 0, 'alice', 100, [1])
        seed_user_arrival(router, 0, 'zed', 200, [2])

        router.maybe_proactive_kv_prewarm(1_000)

        self.assertIn('zed', router._proactive_migrations)
        self.assertNotIn('alice', router._proactive_migrations)


class DestinationSelectionTest(unittest.TestCase):
    def test_selects_lowest_pressure_other_instance(self):
        home = make_scheduler(0)
        mid = make_scheduler(1)
        quiet = make_scheduler(2)
        push_pressure(home, 90_000)     # source, pressured
        push_pressure(mid, 50_000)      # candidate but busier
        router = make_router([home, mid, quiet])
        seed_user_arrival(router, 0, 'alice', 0, [1, 2, 3])

        router.maybe_proactive_kv_prewarm(1_000)

        self.assertEqual(router._proactive_migrations['alice']['target_instance_id'], 2)


class SeedingInstallsRealContentTest(unittest.TestCase):
    def test_seed_migrated_prefix_installs_into_destination_radix_cache(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        push_pressure(home, 90_000)
        router = make_router([home, away])
        tokens = [11, 12, 13, 14]
        seed_user_arrival(router, 0, 'alice', 0, tokens)

        router.maybe_proactive_kv_prewarm(1_000)

        match = away.memory.npu_prefix_cache.match_prefix(tokens)
        self.assertEqual(match.hit_length, len(tokens))


class PreferentialEvictionTest(unittest.TestCase):
    """mark_speculative=True must make never-claimed speculative content
    evict BEFORE genuinely-touched real content, reversing what plain LRU
    would otherwise do (the more-recently-inserted entry normally survives
    longest)."""

    def test_speculative_content_evicts_before_older_real_content(self):
        memory = FakeProactiveMemory()
        real_tokens = list(range(100, 150))          # inserted first (real)
        speculative_tokens = list(range(200, 250))   # inserted second (speculative)

        memory.seed_migrated_prefix(real_tokens, len(real_tokens), mark_speculative=False)
        memory.seed_migrated_prefix(
            speculative_tokens, len(speculative_tokens), mark_speculative=True
        )

        # Under plain LRU the second (more recently touched) insert would
        # survive longest. Assert the opposite: the speculative one goes
        # first even though it's the newer entry.
        memory.npu_prefix_cache.evict(10)

        match_real = memory.npu_prefix_cache.match_prefix(real_tokens)
        match_spec = memory.npu_prefix_cache.match_prefix(speculative_tokens)
        self.assertEqual(match_real.hit_length, len(real_tokens))
        self.assertLess(match_spec.hit_length, len(speculative_tokens))

    def test_speculative_content_becomes_normal_once_actually_hit(self):
        memory = FakeProactiveMemory()
        real_tokens = list(range(100, 150))
        speculative_tokens = list(range(200, 250))

        memory.seed_migrated_prefix(real_tokens, len(real_tokens), mark_speculative=False)
        memory.seed_migrated_prefix(
            speculative_tokens, len(speculative_tokens), mark_speculative=True
        )
        # A real request actually claims the speculative content (Hook 2's
        # match_prefix call) -- this must refresh last_access_time like any
        # other real access, so it's no longer evict-first afterward.
        memory.npu_prefix_cache.match_prefix(speculative_tokens)

        memory.npu_prefix_cache.evict(10)

        match_real = memory.npu_prefix_cache.match_prefix(real_tokens)
        match_spec = memory.npu_prefix_cache.match_prefix(speculative_tokens)
        self.assertLess(match_real.hit_length, len(real_tokens))
        self.assertEqual(match_spec.hit_length, len(speculative_tokens))


class RoutingBindTest(unittest.TestCase):
    """_proactive_pin_candidate binds a real request's redirect decision to
    a user's outstanding pre-warm target, instead of leaving it to the
    normal candidate selector (which re-scores cluster state independently
    and can land on a different, unpre-warmed instance -- the dominant
    "wrong destination" miss mode)."""

    def make_scored(self, *targets):
        return [
            {'target_id': sched.instance_id, 'target': sched}
            for sched in targets
        ]

    def test_no_pending_prewarm_returns_none(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        router = make_router([home, away])
        req_data = {'geo': {'user_id': 'alice'}}

        result = router._proactive_pin_candidate(
            req_data, self.make_scored(away), reuse_tokens=10
        )
        self.assertIsNone(result)

    def test_pending_prewarm_resident_and_admissible_binds(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        push_pressure(home, 90_000)
        router = make_router([home, away])
        tokens = list(range(20))
        seed_user_arrival(router, 0, 'alice', 0, tokens)
        router.maybe_proactive_kv_prewarm(1_000)
        self.assertEqual(
            router._proactive_migrations['alice']['target_instance_id'], 1
        )

        req_data = {'geo': {'user_id': 'alice'}, 'input_hash_ids': tokens,
                    'input_toks': len(tokens)}
        result = router._proactive_pin_candidate(
            req_data, self.make_scored(away), reuse_tokens=len(tokens)
        )
        self.assertIsNotNone(result)
        self.assertEqual(result['target_id'], 1)

    def test_pinned_target_not_in_admissible_scored_falls_back(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        elsewhere = make_scheduler(2)
        push_pressure(home, 90_000)
        router = make_router([home, away, elsewhere])
        tokens = list(range(20))
        seed_user_arrival(router, 0, 'alice', 0, tokens)
        router.maybe_proactive_kv_prewarm(1_000)
        pinned_target = router._proactive_migrations['alice']['target_instance_id']

        req_data = {'geo': {'user_id': 'alice'}, 'input_hash_ids': tokens,
                    'input_toks': len(tokens)}
        # `away` (pinned) filled up in the meantime and dropped out of the
        # admissible/scored set that the caller would pass in -- only
        # `elsewhere` remains admissible.
        other = away if pinned_target != away.instance_id else elsewhere
        result = router._proactive_pin_candidate(
            req_data, self.make_scored(other), reuse_tokens=len(tokens)
        )
        self.assertIsNone(result)

    def test_evicted_content_falls_back(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        push_pressure(home, 90_000)
        router = make_router([home, away])
        tokens = list(range(20))
        seed_user_arrival(router, 0, 'alice', 0, tokens)
        router.maybe_proactive_kv_prewarm(1_000)

        # Simulate the speculative content having been evicted by real
        # traffic before the user's real request arrives.
        away.memory.npu_prefix_cache.evict(10**9)

        req_data = {'geo': {'user_id': 'alice'}, 'input_hash_ids': tokens,
                    'input_toks': len(tokens)}
        result = router._proactive_pin_candidate(
            req_data, self.make_scored(away), reuse_tokens=len(tokens)
        )
        self.assertIsNone(result)

    def test_disabled_flag_never_binds(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        router = make_router([home, away], enable_proactive_kv_prewarm=False)
        req_data = {'geo': {'user_id': 'alice'}, 'input_hash_ids': [1, 2, 3],
                    'input_toks': 3}

        result = router._proactive_pin_candidate(
            req_data, self.make_scored(away), reuse_tokens=3
        )
        self.assertIsNone(result)

    def test_zero_reuse_tokens_never_binds(self):
        home = make_scheduler(0)
        away = make_scheduler(1)
        push_pressure(home, 90_000)
        router = make_router([home, away])
        tokens = list(range(20))
        seed_user_arrival(router, 0, 'alice', 0, tokens)
        router.maybe_proactive_kv_prewarm(1_000)

        req_data = {'geo': {'user_id': 'alice'}, 'input_hash_ids': tokens,
                    'input_toks': len(tokens)}
        result = router._proactive_pin_candidate(
            req_data, self.make_scored(away), reuse_tokens=0
        )
        self.assertIsNone(result)


class PayoffTest(unittest.TestCase):
    def make_pressured_router(self, top_k=3):
        home = make_scheduler(0)
        away = make_scheduler(1)
        push_pressure(home, 90_000)
        router = make_router([home, away], proactive_kv_prewarm_top_k=top_k)
        return router, home, away

    def test_payoff_hit_discounts_migration(self):
        router, home, away = self.make_pressured_router()
        tokens = list(range(50))
        seed_user_arrival(router, 0, 'alice', 0, tokens)
        router.maybe_proactive_kv_prewarm(1_000)
        seeded_tokens = router._proactive_migrations['alice']['seeded_tokens']

        req_data = make_migrate_kv_req_data('alice', tokens, arrival_time_ns=2_000)
        router._resolve_proactive_kv_prewarm(req_data, away)
        self.assertEqual(
            req_data['_proactive_kv_prewarm_seeded_tokens'], seeded_tokens
        )

        router._apply_kv_migration_if_needed(req_data, away)

        self.assertEqual(req_data['geo']['proactive_kv_prewarm_hit'], 1)
        hit_tokens = req_data['geo']['proactive_kv_prewarm_hit_tokens']
        self.assertGreater(hit_tokens, 0)
        self.assertLessEqual(hit_tokens, seeded_tokens)
        self.assertEqual(req_data['geo'].get('proactive_kv_prewarm_wasted'), 0)
        # Billed bytes/latency must be strictly less than the no-credit case.
        baseline_req_data = make_migrate_kv_req_data('bob', tokens, arrival_time_ns=2_000)
        router._apply_kv_migration_if_needed(baseline_req_data, away)
        self.assertLess(
            req_data['failover']['kv_migration_bytes'],
            baseline_req_data['failover']['kv_migration_bytes'],
        )
        self.assertLess(
            req_data['failover']['kv_migration_latency_ns'],
            baseline_req_data['failover']['kv_migration_latency_ns'],
        )

    def test_payoff_miss_when_different_target(self):
        router, home, away = self.make_pressured_router()
        elsewhere = make_scheduler(2)
        tokens = list(range(50))
        seed_user_arrival(router, 0, 'alice', 0, tokens)
        router.maybe_proactive_kv_prewarm(1_000)
        self.assertIn('alice', router._proactive_migrations)

        req_data = make_migrate_kv_req_data('alice', tokens, arrival_time_ns=2_000)
        router._resolve_proactive_kv_prewarm(req_data, elsewhere)

        self.assertNotIn('alice', router._proactive_migrations)
        self.assertEqual(req_data['geo']['proactive_kv_prewarm_wasted'], 1)
        self.assertNotIn('_proactive_kv_prewarm_seeded_tokens', req_data)

        # Applying the real migration on the (different) target must not
        # crash and must not credit anything.
        router._apply_kv_migration_if_needed(req_data, elsewhere)
        self.assertEqual(req_data['geo']['proactive_kv_prewarm_hit_tokens'], 0)
        self.assertEqual(req_data['geo']['proactive_kv_prewarm_hit'], 0)

    def test_payoff_miss_when_request_stays_local(self):
        router, home, away = self.make_pressured_router()
        tokens = list(range(50))
        seed_user_arrival(router, 0, 'alice', 0, tokens)
        router.maybe_proactive_kv_prewarm(1_000)

        req_data = {'geo': {'user_id': 'alice'}}
        router._resolve_proactive_kv_prewarm(req_data, home)

        self.assertEqual(req_data['geo']['proactive_kv_prewarm_wasted'], 1)
        self.assertNotIn('alice', router._proactive_migrations)
        # No failover key at all -- _apply_kv_migration_if_needed must be a
        # harmless no-op (mirrors every other failover_mode branch).
        router._apply_kv_migration_if_needed(req_data, home)

    def test_no_double_credit_on_later_unrelated_request(self):
        router, home, away = self.make_pressured_router()
        tokens = list(range(50))
        seed_user_arrival(router, 0, 'alice', 0, tokens)
        router.maybe_proactive_kv_prewarm(1_000)

        first = make_migrate_kv_req_data('alice', tokens, arrival_time_ns=2_000)
        router._resolve_proactive_kv_prewarm(first, away)
        router._apply_kv_migration_if_needed(first, away)
        self.assertEqual(first['geo']['proactive_kv_prewarm_hit'], 1)

        # A second, later request from the same user must not find a
        # leftover pending entry (it was popped by the first resolution).
        second = make_migrate_kv_req_data('alice', tokens, arrival_time_ns=3_000)
        router._resolve_proactive_kv_prewarm(second, away)
        self.assertNotIn('_proactive_kv_prewarm_seeded_tokens', second)
        router._apply_kv_migration_if_needed(second, away)
        self.assertEqual(second['geo']['proactive_kv_prewarm_hit'], 0)


if __name__ == '__main__':
    unittest.main()
