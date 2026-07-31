"""Scheduler-level safety test for the scheduler-hide KV migration gate
(serving/core/scheduler.py's schedule_base/schedule_with_prefix).

The gate ("a prefill request whose kv_ready_time_ns is still in the future
is excluded from batch_req for this attempt") is inserted identically in
both schedule_base and schedule_with_prefix, immediately after batch_req is
first built from self.request and before any of the token-budget/eviction
machinery runs. This test exercises schedule_base (the simpler,
enable_prefix_caching=False path) end to end: it constructs a real
Scheduler instance (bypassing __init__, which requires a fully-configured
MemoryModel/model config) with a minimal fake memory model, and confirms
the one invariant that matters -- a KV-pending request is never removed
from self.request and never marked as scheduled while gated, only once
kv_ready_time_ns has passed. schedule_with_prefix shares the exact same
gate placement and STEP3/4 self.request-deletion pattern, so this is the
representative regression test for both.
"""

import unittest
from types import SimpleNamespace

from serving.core.scheduler import Scheduler
from serving.core.request import Request


class FakeSchedulerMemory:
    """Just enough of MemoryModel's contract to drive schedule_base's
    non-eviction path: every request always fits, nothing is ever evicted."""

    def get_block_kv(self, batch_req, batch_len, scheduled_tokens=None):
        return 1

    def is_avail(self, size, device):
        return True

    def allocate(self, size, device):
        pass

    def free(self, size, device):
        pass

    def get_evict_kv(self, req):
        return 0


def make_bare_scheduler(max_num_seqs=8, max_num_batched_tokens=1000):
    """A real Scheduler instance with __init__ bypassed (it requires a
    fully real MemoryModel constructed from a model config file), all
    attributes schedule_base actually touches set directly."""
    scheduler = object.__new__(Scheduler)
    scheduler.model = 'fake-model'
    scheduler.instance_id = 0
    scheduler.start_npu = 0
    scheduler.pp_size = 1
    scheduler.max_num_seqs = max_num_seqs
    scheduler.max_num_batched_tokens = max_num_batched_tokens
    scheduler.long_prefill_token_threshold = 0
    scheduler.prioritize_prefill = False
    scheduler.enable_chunked_prefill = False
    scheduler.request = []
    scheduler.inflight = []
    scheduler.done = []
    scheduler.batch_ids = -1
    scheduler.batch_busy_intervals_ns = []
    scheduler.memory = FakeSchedulerMemory()
    scheduler.logger = SimpleNamespace(info=lambda *a, **k: None)
    return scheduler


def make_prefill_request(request_id, arrival_ns, kv_ready_time_ns=None):
    req = Request(request_id, 'fake-model', 100, 120, arrival_ns, 0)
    req.kv_ready_time_ns = (
        arrival_ns if kv_ready_time_ns is None else kv_ready_time_ns
    )
    return req


class SchedulerKvReadyGateTest(unittest.TestCase):
    def test_kv_pending_request_is_excluded_but_not_removed(self):
        scheduler = make_bare_scheduler()
        pending = make_prefill_request(1, arrival_ns=1_000, kv_ready_time_ns=5_000)
        scheduler.request = [pending]

        batch = scheduler.schedule_base(current=2_000, sys=0)

        self.assertIsNone(batch)
        # Still present, untouched -- not marked scheduled, not deleted.
        self.assertIn(pending, scheduler.request)
        self.assertEqual(pending.first_schedule_time_ns, -1)
        self.assertEqual(len(scheduler.done), 0)

    def test_request_becomes_schedulable_once_kv_ready_time_passes(self):
        scheduler = make_bare_scheduler()
        request = make_prefill_request(1, arrival_ns=1_000, kv_ready_time_ns=5_000)
        scheduler.request = [request]

        still_pending = scheduler.schedule_base(current=2_000, sys=0)
        self.assertIsNone(still_pending)
        self.assertIn(request, scheduler.request)

        batch = scheduler.schedule_base(current=5_000, sys=0)

        self.assertIsNotNone(batch)
        self.assertNotIn(request, scheduler.request)
        self.assertNotEqual(request.first_schedule_time_ns, -1)

    def test_default_kv_ready_time_equals_arrival_is_a_no_op(self):
        """kv_ready_time_ns defaults to arrival (Request.__init__), so a
        request never touched by the scheduler-hide flag schedules exactly
        as before."""
        scheduler = make_bare_scheduler()
        request = make_prefill_request(1, arrival_ns=1_000)  # kv_ready_time_ns == arrival
        scheduler.request = [request]

        batch = scheduler.schedule_base(current=1_000, sys=0)

        self.assertIsNotNone(batch)
        self.assertNotIn(request, scheduler.request)

    def test_mixed_batch_only_kv_pending_request_is_excluded(self):
        scheduler = make_bare_scheduler()
        ready = make_prefill_request(1, arrival_ns=1_000)
        pending = make_prefill_request(2, arrival_ns=1_000, kv_ready_time_ns=5_000)
        scheduler.request = [ready, pending]

        batch = scheduler.schedule_base(current=2_000, sys=0)

        self.assertIsNotNone(batch)
        self.assertNotIn(ready, scheduler.request)
        self.assertIn(pending, scheduler.request)
        self.assertEqual(pending.first_schedule_time_ns, -1)

    def test_decode_request_is_never_gated(self):
        scheduler = make_bare_scheduler()
        decode = make_prefill_request(1, arrival_ns=1_000, kv_ready_time_ns=5_000)
        decode.num_computed_tokens = decode.original_input  # is_prefill() -> False
        scheduler.request = [decode]

        batch = scheduler.schedule_base(current=2_000, sys=0)

        self.assertIsNotNone(batch)
        self.assertNotIn(decode, scheduler.request)


if __name__ == '__main__':
    unittest.main()
