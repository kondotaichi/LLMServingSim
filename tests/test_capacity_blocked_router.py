import sys
import types
import unittest
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
    npu_used = 0

    @staticmethod
    def get_kv(tokens):
        return int(tokens) * 10

    @staticmethod
    def avail_size(_device):
        return 1_000_000


class FakeRequest:
    def __init__(self, request_id, output_tokens):
        self.id = request_id
        self.output = output_tokens


def make_scheduler(instance_id, requests=None):
    return SimpleNamespace(
        instance_id=instance_id,
        pd_type='prefill',
        request=list(requests or []),
        inflight=[],
        max_num_seqs=8,
        memory=FakeMemory(),
    )


class CapacityBlockedRouterTest(unittest.TestCase):
    def test_blocked_request_is_not_released_without_capacity_change(self):
        active = FakeRequest(10, 1_000)
        scheduler = make_scheduler(0, [active])
        router = Router(1, [scheduler], 1, routing_policy='NEAREST_KV')
        request = {
            'index': 1,
            'input_toks': 100,
            'output_toks': 200,
            'arrival_time_ns': 1_000,
            'geo': {},
        }

        router._last_capacity_signature = router._capacity_signature()
        router._defer_capacity_retry(request, 2_000, scheduler, scheduler)
        router._requeue_deferred_request(request)

        self.assertEqual(request['arrival_time_ns'], 1_000)
        self.assertEqual(len(router._capacity_blocked_requests), 1)
        self.assertEqual(router._release_capacity_blocked_if_changed(), 0)
        self.assertEqual(len(router._capacity_blocked_requests), 1)
        self.assertFalse(router._pending_requests)

    def test_capacity_change_releases_blocked_request_once(self):
        active = FakeRequest(10, 1_000)
        scheduler = make_scheduler(0, [active])
        router = Router(1, [scheduler], 1, routing_policy='NEAREST_KV')
        request = {
            'index': 1,
            'input_toks': 100,
            'output_toks': 200,
            'arrival_time_ns': 1_000,
            'geo': {},
        }

        router._last_capacity_signature = router._capacity_signature()
        router._defer_capacity_retry(request, 2_000, scheduler, scheduler)
        router._requeue_deferred_request(request)
        scheduler.request.clear()

        self.assertEqual(router._release_capacity_blocked_if_changed(), 1)
        self.assertFalse(router._capacity_blocked_requests)
        self.assertEqual(router._pending_requests, [request])
        self.assertNotIn('_capacity_blocked', request)
        self.assertEqual(router._release_capacity_blocked_if_changed(), 0)

    def test_added_load_does_not_release_blocked_request(self):
        scheduler = make_scheduler(0)
        router = Router(1, [scheduler], 1, routing_policy='NEAREST_KV')
        request = {
            'index': 1,
            'input_toks': 100,
            'output_toks': 200,
            'arrival_time_ns': 1_000,
            'geo': {},
        }

        router._last_capacity_signature = router._capacity_signature()
        router._defer_capacity_retry(request, 2_000, scheduler, scheduler)
        router._requeue_deferred_request(request)
        scheduler.request.append(FakeRequest(10, 1_000))

        self.assertEqual(router._release_capacity_blocked_if_changed(), 0)
        self.assertEqual(len(router._capacity_blocked_requests), 1)
        self.assertFalse(router._pending_requests)

    def test_pending_state_includes_capacity_blocked_requests(self):
        scheduler = make_scheduler(0)
        router = Router(1, [scheduler], 1, routing_policy='NEAREST_KV')
        router._capacity_blocked_requests.append({'index': 1})

        self.assertTrue(router.has_pending_requests())
        self.assertIsNone(router.get_next_pending_arrival())


if __name__ == '__main__':
    unittest.main()
