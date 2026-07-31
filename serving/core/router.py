import bisect
import csv
import json
import os
import random
from collections import deque
from .logger import get_logger
from .memory_model import Device

DEFAULT_KV_MIGRATION_BANDWIDTH_GBPS = 100.0
DEFAULT_KV_MIGRATION_DISTANCE_M = 10_000.0
DEFAULT_DISTANCE_LATENCY_NS_PER_M = 5.0

MULTI_CANDIDATE_SELECTORS = {
    'NEAREST_CAPACITY_MULTI_FORMULA_KV_RESERVE': 'formula',
    'NEAREST_CAPACITY_MULTI_WAITING_FORMULA_KV_RESERVE': 'min_waiting',
    'NEAREST_CAPACITY_MULTI_PRESSURE_FORMULA_KV_RESERVE': 'min_pressure',
    'NEAREST_CAPACITY_MULTI_RANDOM_FORMULA_KV_RESERVE': 'random',
    'NEAREST_CAPACITY_MULTI_PRESSURE_KV_RESERVE': 'min_pressure_no_model',
}
FORMULA_MULTI_CANDIDATE_POLICIES = tuple(
    policy for policy, selector in MULTI_CANDIDATE_SELECTORS.items()
    if selector != 'min_pressure_no_model'
)


# Geographic/communication fields written by
# `python -m workloads.generators geographic` (Phase 1 spec section 20/22.3).
# Present only on geographic workloads; absent on ordinary flat JSONL rows.
_GEO_FIELDS = (
    'user_id', 'user_x_m', 'user_y_m',
    'gpu_id', 'gpu_x_m', 'gpu_y_m', 'distance_m', 'assigned_instance_id',
    # SPEC: these 3 fields are new (redirect-on-capacity routing) — read from
    # the geographic workload's per-request row via _two_nearest_gpus() in
    # workloads/generators/geographic.py, needed by NEAREST_REJECT/NEAREST_MIGRATE.
    'second_nearest_gpu_id', 'second_nearest_distance_m', 'distance_latency_ns_per_meter',
    'network_throughput_mbps',
    'request_payload_bytes', 'first_token_payload_bytes',
    'uplink_distance_latency_ns', 'uplink_serialization_latency_ns', 'uplink_latency_ns',
    'downlink_distance_latency_ns', 'downlink_serialization_latency_ns', 'downlink_latency_ns',
    'communication_latency_ns',
    'request_send_time_ns',
    'redirect_capacity_reason',
    'capacity_running_reqs', 'capacity_max_num_seqs',
    'capacity_required_kv_bytes', 'capacity_free_npu_bytes',
)


def _extract_geo_fields(row):
    """Pull the optional geographic/communication fields out of a JSONL row.

    Returns None (not an empty dict) when none are present, so callers can
    cheaply tell "old-format workload" from "geographic workload".
    """
    geo = {k: row[k] for k in _GEO_FIELDS if k in row}
    return geo or None


class Router:
    def __init__(
            self,
            num_instances,
            schedulers, req_num,
            routing_policy="RR",
            seed=42,
            # SPEC: these 2 kwargs are new (base signature ended at `seed=42`).
            # Only consumed by NEAREST_MIGRATE's GPU-to-GPU backbone forward.
            gpu_backbone_bandwidth_gbps=None,
            gpu_backbone_distance_m=None,
            # SPEC: fixed-RTT APN model (10cell_apn spec section 6). When set,
            # replaces every distance-proportional propagation term in
            # _maybe_reject_and_redirect / _maybe_migrate_and_redirect /
            # _apply_kv_migration_if_needed with this constant, since a real
            # APN link's one-way propagation is uniform across node pairs
            # rather than physical-distance-proportional.
            apn_fixed_propagation_ns=None,
            # SPEC: CPU-staging KV migration time model (10cell_apn spec
            # section 8.3). Both must be set together to activate; otherwise
            # KV migration keeps the single-hop distance+serialization model.
            kv_staging_bandwidth_gbytes_per_s=None,
            kv_staging_latency_ns=None,
            adaptive_token_time_ns=100_000.0,
            adaptive_iteration_time_ns=1_000_000.0,
            oneshot_redirect_margin_ns=200_000_000.0,
            oneshot_max_local_wait_ns=1_000_000_000.0,
            enable_oneshot_target_reservation=True,
            ttft_formula_artifact_dir=None,
            counterfactual_request_id=None,
            counterfactual_target_instance_id=None,
            counterfactual_force_local_request_id=None,
            # SPEC: 2026-07-27_speculative_kv_transfer -- see
            # experiments/2026-07-22_pp2_five_workloads for the comparison
            # experiment these three flags drive. All default False/off and
            # reproduce pre-existing behavior exactly when unset.
            enable_scheduler_hide_kv_migration=False,
            enable_formula_local_wait_point_estimate=False,
            enable_speculative_kv_migration=False,
            # SPEC: 2026-07-28_proactive_kv_prewarm -- Method C (capacity-
            # pressure-triggered proactive KV pre-migration). Independent of
            # Method A/B: request-agnostic background process, not a
            # per-request routing decision. See
            # experiments/2026-07-28_pp_schedule_conceal_and_speculative/
            # reports/proactive_kv_prewarm_design.md. All default off/no-op.
            enable_proactive_kv_prewarm=False,
            proactive_kv_prewarm_pressure_threshold=0.8,
            proactive_kv_prewarm_top_k=3,
            proactive_kv_prewarm_lookback_ns=30_000_000_000.0,
            proactive_kv_prewarm_cooldown_ns=2_000_000_000.0,
            proactive_kv_prewarm_eval_interval_ns=200_000_000.0,
    ):
        # SPEC: stored for NEAREST_MIGRATE (see _maybe_migrate_and_redirect below).
        self.gpu_backbone_bandwidth_gbps = gpu_backbone_bandwidth_gbps
        self.gpu_backbone_distance_m = gpu_backbone_distance_m
        self.apn_fixed_propagation_ns = apn_fixed_propagation_ns
        self.kv_staging_bandwidth_gbytes_per_s = kv_staging_bandwidth_gbytes_per_s
        self.kv_staging_latency_ns = kv_staging_latency_ns
        self.adaptive_token_time_ns = float(adaptive_token_time_ns)
        self.adaptive_iteration_time_ns = float(adaptive_iteration_time_ns)
        self.oneshot_redirect_margin_ns = float(oneshot_redirect_margin_ns)
        self.oneshot_max_local_wait_ns = float(oneshot_max_local_wait_ns)
        self.enable_oneshot_target_reservation = bool(enable_oneshot_target_reservation)
        self.counterfactual_request_id = counterfactual_request_id
        self.counterfactual_target_instance_id = counterfactual_target_instance_id
        if ((counterfactual_request_id is None)
                != (counterfactual_target_instance_id is None)):
            raise ValueError(
                "Counterfactual request and target IDs must be set together"
            )
        # SPEC: 2026-07-25_for_speculative_test -- force one specific
        # request to wait at its home GPU instead of ever being redirected,
        # regardless of predicted local wait or admissibility, to measure
        # the true home-vs-redirect TTFT (the existing counterfactual_*
        # pair above can only force a choice among already-admissible
        # candidates, never home itself, since home is excluded from
        # `admissible` precisely because it wasn't admissible in baseline).
        self.counterfactual_force_local_request_id = (
            counterfactual_force_local_request_id
        )
        if (counterfactual_request_id is not None
                and counterfactual_force_local_request_id is not None):
            raise ValueError(
                "counterfactual_force_local_request_id cannot be combined "
                "with counterfactual_request_id/counterfactual_target_instance_id"
            )
        # SPEC: 2026-07-27_speculative_kv_transfer -- Method A (parallelize
        # KV migration transfer with target-scheduler queueing) and Method B
        # (speculatively pre-transfer KV to a pinned candidate before the
        # redirect decision is final). See
        # experiments/2026-07-22_pp2_five_workloads/scripts/analyze_pp_comparison.py
        # for the comparison these are built for.
        self.enable_scheduler_hide_kv_migration = bool(enable_scheduler_hide_kv_migration)
        self.enable_formula_local_wait_point_estimate = bool(
            enable_formula_local_wait_point_estimate
        )
        self.enable_speculative_kv_migration = bool(enable_speculative_kv_migration)
        if self.enable_speculative_kv_migration and not self.enable_scheduler_hide_kv_migration:
            raise ValueError(
                "enable_speculative_kv_migration requires "
                "enable_scheduler_hide_kv_migration (speculative pre-transfer "
                "only has a window to hide once the request is visible to the "
                "target scheduler before its KV bytes arrive)"
            )
        # SPEC: 2026-07-28_proactive_kv_prewarm -- Method C. No cross-flag
        # requirement on Method A/B: Method C discounts migration_bytes
        # itself (see _apply_kv_migration_if_needed), so it pays off even
        # with the pre-existing serial arrival_time_ns += migration_ns model.
        self.enable_proactive_kv_prewarm = bool(enable_proactive_kv_prewarm)
        self.proactive_kv_prewarm_pressure_threshold = float(
            proactive_kv_prewarm_pressure_threshold
        )
        self.proactive_kv_prewarm_top_k = int(proactive_kv_prewarm_top_k)
        self.proactive_kv_prewarm_lookback_ns = float(proactive_kv_prewarm_lookback_ns)
        self.proactive_kv_prewarm_cooldown_ns = float(proactive_kv_prewarm_cooldown_ns)
        self.proactive_kv_prewarm_eval_interval_ns = float(
            proactive_kv_prewarm_eval_interval_ns
        )
        # instance_id -> {user_id -> deque[arrival_ns]}, recent-arrival
        # history used only for the top-K frequency ranking above.
        self._instance_user_history = {}
        # user_id -> {input_hash_ids, input_toks, home_instance_id, seen_at_ns}
        self._user_last_seen_content = {}
        self._proactive_last_trigger_ns = {}   # instance_id -> ns (per-instance cooldown)
        self._proactive_last_scan_ns = None    # global eval-interval throttle
        # user_id -> {target_instance_id, source_instance_id, seeded_tokens, seeded_at_ns}
        # At most one outstanding entry per user_id at a time.
        self._proactive_migrations = {}
        self.proactive_migration_log = []      # one row per trigger event (diagnostics)
        self.ttft_formula = None
        if self.adaptive_token_time_ns <= 0 or self.adaptive_iteration_time_ns <= 0:
            raise ValueError("Adaptive routing time estimates must be positive")
        if self.oneshot_redirect_margin_ns < 0 or self.oneshot_max_local_wait_ns < 0:
            raise ValueError("One-shot routing margin and local-wait limit must be non-negative")
        self._adaptive_reservations = {}
        self.schedulers = schedulers
        self.num_instances = num_instances
        self.prefill_schedulers = [s for s in schedulers if s.pd_type != "decode"]
        self.prefill_instances = len(self.prefill_schedulers)
        self.decode_schedulers = [s for s in schedulers if s.pd_type == "decode"]
        self.decode_instances = len(self.decode_schedulers)
        self.req_num = req_num
        self.routing_policy = routing_policy.upper()
        self.seed = seed
        self._rnd = random.Random(seed) if seed is not None else random
        self.prefill_rr_counter = 0
        self.decode_rr_counter = 0

        # Pending requests (loaded but not yet routed)
        self._pending_requests = []
        self._pending_idx = 0
        self._enable_prefix_caching = False
        self._is_init = True

        # Agentic session dependency tracking
        self._deferred_sessions = {}     # session_id -> session state dict
        self._request_to_session = {}    # request_id -> (session_id, sub_request_index)
        self._next_request_id = 0        # monotonic counter for unique request IDs
        self.candidate_diagnostics = []

        if self.routing_policy == "RR":
            self._select_instance = self._rr_select
        elif self.routing_policy == "RAND":
            self._select_instance = self._rand_select
        elif self.routing_policy == "LOAD":
            self._select_instance = self._least_load_select
        elif self.routing_policy == "PROMPT":
            self._select_instance = self._prompt_length_select
        elif self.routing_policy == "QUEUE":
            self._select_instance = self._queue_select
        elif self.routing_policy == "HYBRID":
            self._select_instance = self._hybrid_select
        elif self.routing_policy == "CUSTOM":
            self._select_instance = self._custom_select
        elif self.routing_policy == "NEAREST":
            self._select_instance = self._nearest_select
        elif self.routing_policy == "NEAREST_KV":
            self._select_instance = self._nearest_select
        # >>> SPEC: redirect-on-capacity routing — base only had "NEAREST" above.
        # Both new policies reuse _nearest_select for the *baseline* instance
        # pick; the redirect decision itself happens earlier, in
        # route_arrived_requests(), before _select_instance is ever called.
        elif self.routing_policy == "NEAREST_REJECT":
            self._select_instance = self._nearest_select
        elif self.routing_policy == "NEAREST_MIGRATE":
            self._select_instance = self._nearest_select
        elif self.routing_policy == "NEAREST_MIGRATE_KV":
            self._select_instance = self._nearest_select
        elif self.routing_policy == "NEAREST_SECOND_TTFT_RESERVE":
            self._select_instance = self._nearest_select
        elif self.routing_policy == "NEAREST_CAPACITY_ONESHOT_KV_RESERVE":
            self._select_instance = self._nearest_select
        elif self.routing_policy == "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE":
            self._select_instance = self._nearest_select
        elif self.routing_policy in (
            "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE",
            *MULTI_CANDIDATE_SELECTORS,
        ):
            self._select_instance = self._nearest_select
        # <<< SPEC: redirect-on-capacity routing
        else:
            raise ValueError(f"Unknown routing_policy '{routing_policy}'. "
                             "Supported: RR, RAND, LOAD, PROMPT, QUEUE, HYBRID, CUSTOM, NEAREST, "
                             "NEAREST_KV, NEAREST_REJECT, NEAREST_MIGRATE, NEAREST_MIGRATE_KV, "
                             "NEAREST_SECOND_TTFT_RESERVE, NEAREST_CAPACITY_ONESHOT_KV_RESERVE, "
                             "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE, "
                             "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE, "
                             + ", ".join(MULTI_CANDIDATE_SELECTORS))
        if self.routing_policy in (
            "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE",
            "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE",
            *FORMULA_MULTI_CANDIDATE_POLICIES,
        ):
            from .ttft_formula import OfflineTtftFormula
            self.ttft_formula = OfflineTtftFormula(ttft_formula_artifact_dir)
        self.logger = get_logger(self.__class__)

    def save_candidate_diagnostics(self, output_file):
        """Write one row per evaluated redirect candidate."""
        output_dir = os.path.dirname(output_file)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        fieldnames = []
        seen = set()
        for row in self.candidate_diagnostics:
            for key in row:
                if key not in seen:
                    fieldnames.append(key)
                    seen.add(key)
        with open(output_file, 'w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.candidate_diagnostics)

    # -----------------------------------------------------------------------
    # Instance selection policies
    # -----------------------------------------------------------------------

    def _get_counter(self, role):
        return self.decode_rr_counter if role == "decode" else self.prefill_rr_counter

    def _set_counter(self, role, value):
        if role == "decode":
            self.decode_rr_counter = value
        else:
            self.prefill_rr_counter = value

    def _rr_select(self, schedulers, role, req_data=None):
        num_instances = len(schedulers)
        idx = self._get_counter(role) % num_instances
        self._set_counter(role, idx + 1)
        return idx

    def _rand_select(self, schedulers, role, req_data=None):
        return self._rnd.randrange(len(schedulers))

    def _least_load_select(self, schedulers, role, req_data=None):
        """vLLM-style least-loaded routing, normalized by instance capacity."""
        best_idx = 0
        best_score = float('inf')
        num_instances = len(schedulers)
        start = self._get_counter(role) % num_instances
        for offset in range(num_instances):
            idx = (start + offset) % num_instances
            sched = schedulers[idx]
            waiting = len(sched.request)
            running = sum(len(b.requests) for b in sched.inflight)
            raw_score = waiting * 4 + running
            capacity = getattr(sched, "max_num_seqs", 0)
            score = raw_score
            if capacity not in (0, float('inf')):
                score = raw_score / capacity
            if score < best_score:
                best_score = score
                best_idx = idx
        self._set_counter(role, (best_idx + 1) % num_instances)
        return best_idx

    def _queue_score(self, sched):
        waiting = len(sched.request)
        running = sum(len(b.requests) for b in sched.inflight)
        return waiting * 4 + running

    def _token_capacity(self, sched):
        cap = sched.max_num_batched_tokens
        if cap == 0:
            return float('inf')
        return cap

    def _prompt_length_select(self, schedulers, role, req_data=None):
        """Route short prompts to small token-budget instances and long prompts to large ones."""
        prompt_len = int(req_data['input_toks']) if req_data else 0
        best_idx = 0
        best_key = None
        num_instances = len(schedulers)
        start = self._get_counter(role) % num_instances
        for offset in range(num_instances):
            idx = (start + offset) % num_instances
            sched = schedulers[idx]
            cap = self._token_capacity(sched)
            queue = self._queue_score(sched)
            if cap >= prompt_len:
                key = (0, cap, queue, offset)
            else:
                key = (1, -cap, queue, offset)
            if best_key is None or key < best_key:
                best_key = key
                best_idx = idx
        self._set_counter(role, (best_idx + 1) % num_instances)
        return best_idx

    def _queue_select(self, schedulers, role, req_data=None):
        """Route to the instance with the smallest current queue pressure."""
        best_idx = 0
        best_key = None
        num_instances = len(schedulers)
        start = self._get_counter(role) % num_instances
        for offset in range(num_instances):
            idx = (start + offset) % num_instances
            sched = schedulers[idx]
            key = (self._queue_score(sched), offset)
            if best_key is None or key < best_key:
                best_key = key
                best_idx = idx
        self._set_counter(role, (best_idx + 1) % num_instances)
        return best_idx

    def _hybrid_select(self, schedulers, role, req_data=None):
        """Blend prompt-length affinity with current queue pressure."""
        prompt_len = max(1, int(req_data['input_toks']) if req_data else 1)
        best_idx = 0
        best_score = float('inf')
        num_instances = len(schedulers)
        start = self._get_counter(role) % num_instances
        max_queue = max((self._queue_score(s) for s in schedulers), default=0)
        queue_denom = max(1, max_queue)
        finite_caps = [self._token_capacity(s) for s in schedulers
                       if self._token_capacity(s) != float('inf')]
        max_finite_cap = max(finite_caps, default=prompt_len)

        for offset in range(num_instances):
            idx = (start + offset) % num_instances
            sched = schedulers[idx]
            cap = self._token_capacity(sched)
            effective_cap = max_finite_cap * 2 if cap == float('inf') else cap
            if cap >= prompt_len:
                prompt_score = abs(effective_cap - prompt_len) / max(1, effective_cap)
            else:
                prompt_score = 1.0 + (prompt_len - effective_cap) / max(1, prompt_len)
            queue_score = self._queue_score(sched) / queue_denom
            score = prompt_score + queue_score
            if score < best_score:
                best_score = score
                best_idx = idx
        self._set_counter(role, (best_idx + 1) % num_instances)
        return best_idx

    def _custom_select(self, schedulers, role, req_data=None):
        raise NotImplementedError("Implement custom routing policy.")

    def _nearest_select(self, schedulers, role, req_data=None):
        """Route to the pre-assigned nearest-GPU instance (geographic workloads).

        Uses ``assigned_instance_id`` computed offline by the geographic
        workload generator. No load/queue/KV-cache-based reselection.
        """
        if req_data is None or 'assigned_instance_id' not in req_data:
            raise RuntimeError(
                "NEAREST routing policy requires 'assigned_instance_id' in the "
                "workload (generate it with `python -m workloads.generators geographic`)."
            )
        target_instance_id = req_data['assigned_instance_id']
        for idx, sched in enumerate(schedulers):
            if sched.instance_id == target_instance_id:
                return idx
        raise RuntimeError(
            f"NEAREST routing: assigned_instance_id {target_instance_id} does not "
            f"match any available {role} instance (valid ids: "
            f"{[s.instance_id for s in schedulers]})."
        )

    # >>> SPEC: redirect-on-capacity routing — everything from here down to the
    # "Request loading and real-time routing" section header is new (base
    # router.py ended the instance-selection-policies section at
    # `_nearest_select` above). See Diary/code/2026-07-03-nearest-reject-
    # capacity-routing.md (NEAREST_REJECT) and Diary/implementation/
    # 20260705_experiment_1.md (NEAREST_MIGRATE) for the full design writeup.

    @staticmethod
    def _find_scheduler(schedulers, instance_id):
        for sched in schedulers:
            if sched.instance_id == instance_id:
                return sched
        return None

    @staticmethod
    def _find_scheduler_index(schedulers, instance_id):
        for idx, sched in enumerate(schedulers):
            if sched.instance_id == instance_id:
                return idx
        return None

    @staticmethod
    def _active_requests(sched):
        """Return unique waiting and inflight requests already admitted."""
        active = {}
        for req in sched.request:
            active[req.id] = req
        for batch in sched.inflight:
            for req in batch.requests:
                active[req.id] = req
        return active.values()

    @staticmethod
    def _full_request_kv_bytes(sched, total_tokens):
        block_size = max(1, int(sched.memory.block_size))
        blocks = (max(0, int(total_tokens)) + block_size - 1) // block_size
        return sched.memory.get_kv(blocks * block_size)

    def _capacity_snapshot(self, sched, req_data):
        """Return an admission snapshot without mutating request state."""
        waiting_reqs = len(sched.request)
        running_reqs = sum(len(batch.requests) for batch in sched.inflight)
        projected_active_kv_bytes = sum(
            self._full_request_kv_bytes(sched, req.output)
            for req in self._active_requests(sched)
        )
        required_kv_bytes = self._full_request_kv_bytes(
            sched, req_data.get('output_toks', 0)
        )
        reservations = self._adaptive_reservations.get(sched.instance_id, {})
        own_id = req_data.get('index')
        other_reservations = [
            reservation for request_id, reservation in reservations.items()
            if request_id != own_id
        ]
        reserved_kv_bytes = sum(r['kv_bytes'] for r in other_reservations)
        reserved_slots = len(other_reservations)
        reserved_prefill_tokens = sum(r['prefill_tokens'] for r in other_reservations)
        projected_active_kv_bytes += reserved_kv_bytes
        if sched.memory.enable_prefix_caching:
            kv_budget_bytes = sched.memory.mem_for_kv
            free_npu_bytes = sched.memory.avail_size(Device.NPU)
        else:
            kv_budget_bytes = sched.memory.npu_mem - sched.memory.weight
            free_npu_bytes = sched.memory.npu_mem - sched.memory.npu_used
        available_kv_bytes = max(0, kv_budget_bytes - projected_active_kv_bytes)
        max_num_seqs = int(sched.max_num_seqs)
        slot_available = running_reqs + reserved_slots < max_num_seqs
        memory_available = required_kv_bytes <= available_kv_bytes
        capacity_pressure = (
            (projected_active_kv_bytes + required_kv_bytes) / kv_budget_bytes
            if kv_budget_bytes > 0 else float('inf')
        )
        slot_pressure = (
            (running_reqs + reserved_slots + 1) / max_num_seqs
            if max_num_seqs > 0 else float('inf')
        )
        return {
            'instance_id': sched.instance_id,
            'waiting_reqs': waiting_reqs,
            'running_reqs': running_reqs,
            'max_num_seqs': max_num_seqs,
            'required_kv_bytes': required_kv_bytes,
            'free_npu_bytes': free_npu_bytes,
            'projected_active_kv_bytes': projected_active_kv_bytes,
            'kv_budget_bytes': kv_budget_bytes,
            'available_kv_bytes': available_kv_bytes,
            'reserved_kv_bytes': reserved_kv_bytes,
            'reserved_slots': reserved_slots,
            'reserved_prefill_tokens': reserved_prefill_tokens,
            'capacity_pressure': capacity_pressure,
            'slot_pressure': slot_pressure,
            'admissible': int(slot_available and memory_available),
        }

    @staticmethod
    def _write_capacity_snapshot(geo, prefix, snapshot):
        for key, value in snapshot.items():
            geo[f'{prefix}_{key}'] = value

    def _record_initial_capacity_context(self, req_data):
        """Persist home and candidate capacity state at the first route attempt."""
        if req_data.get('_router_initial_capacity_recorded', False):
            return
        geo = req_data.get('geo')
        if geo is None:
            return
        home_sched = self._find_scheduler(
            self.prefill_schedulers, req_data.get('assigned_instance_id')
        )
        if home_sched is None:
            return
        home_snapshot = self._capacity_snapshot(home_sched, req_data)
        self._write_capacity_snapshot(geo, 'router_initial', home_snapshot)

        target_gpu_id = geo.get('second_nearest_gpu_id')
        target_sched = self._find_scheduler(
            self.prefill_schedulers, target_gpu_id
        ) if target_gpu_id is not None else None
        if target_sched is not None:
            target_snapshot = self._capacity_snapshot(target_sched, req_data)
            self._write_capacity_snapshot(
                geo, 'router_initial_target', target_snapshot
            )

        candidate_snapshots = [
            self._capacity_snapshot(sched, req_data)
            for sched in self.prefill_schedulers
        ]
        geo['router_initial_candidate_count'] = len(candidate_snapshots)
        geo['router_initial_admissible_candidate_count'] = sum(
            snapshot['admissible'] for snapshot in candidate_snapshots
        )
        geo['router_initial_total_waiting_reqs'] = sum(
            snapshot['waiting_reqs'] for snapshot in candidate_snapshots
        )
        geo['router_initial_max_waiting_reqs'] = max(
            snapshot['waiting_reqs'] for snapshot in candidate_snapshots
        )
        geo['router_initial_total_running_reqs'] = sum(
            snapshot['running_reqs'] for snapshot in candidate_snapshots
        )
        geo['router_initial_max_running_reqs'] = max(
            snapshot['running_reqs'] for snapshot in candidate_snapshots
        )
        geo['router_initial_min_available_kv_bytes'] = min(
            snapshot['available_kv_bytes'] for snapshot in candidate_snapshots
        )
        geo['router_initial_max_available_kv_bytes'] = max(
            snapshot['available_kv_bytes'] for snapshot in candidate_snapshots
        )
        geo['router_initial_min_capacity_pressure'] = min(
            snapshot['capacity_pressure'] for snapshot in candidate_snapshots
        )
        geo['router_initial_max_capacity_pressure'] = max(
            snapshot['capacity_pressure'] for snapshot in candidate_snapshots
        )
        req_data['_router_initial_capacity_recorded'] = True

    def _record_user_activity(self, req_data, current_time_ns):
        """Track per-user recent-arrival frequency and last-seen prompt
        content, keyed by the request's HOME instance (assigned_instance_id
        before any redirect logic runs). Feeds Method C's top-K frequency
        ranking and destination-content seeding -- see
        experiments/2026-07-28_pp_schedule_conceal_and_speculative/reports/
        proactive_kv_prewarm_design.md. Idempotency-guarded the same way as
        _record_initial_capacity_context: route_arrived_requests' loop can
        re-run for the same request across deferred retries, but this must
        record exactly once, at first arrival, before any redirect can
        rewrite assigned_instance_id."""
        if not self.enable_proactive_kv_prewarm:
            return
        if req_data.get('_router_user_activity_recorded', False):
            return
        req_data['_router_user_activity_recorded'] = True
        geo = req_data.get('geo')
        user_id = geo.get('user_id') if geo else None
        home_instance_id = req_data.get('assigned_instance_id')
        if user_id is None or home_instance_id is None:
            return

        history = self._instance_user_history.setdefault(home_instance_id, {})
        history.setdefault(user_id, deque()).append(int(current_time_ns))

        input_hash_ids = req_data.get('input_hash_ids')
        if not input_hash_ids:
            input_hash_ids = list(range(int(req_data.get('input_toks', 0))))
        # Seed only the REUSABLE portion (reuse_prefix_toks, same field the
        # real migrate_kv path uses -- router.py's _apply_kv_migration_if_needed
        # requested_prefix), not the full prompt length. Seeding the whole
        # prompt over-requests destination NPU memory for no benefit (a real
        # migration would only ever ask for reuse_prefix_toks worth) and can
        # exhaust capacity outright on tightly-packed topologies (found via
        # PP1 verification: seed_migrated_prefix raised "not enough NPU
        # memory" when this incorrectly passed the full 8000-token prompt).
        reuse_prefix_toks = min(
            int(req_data.get('reuse_prefix_toks', 0)), int(req_data.get('input_toks', 0))
        )
        self._user_last_seen_content[user_id] = {
            'input_hash_ids': input_hash_ids,
            'reuse_prefix_toks': reuse_prefix_toks,
            'home_instance_id': home_instance_id,
            'seen_at_ns': int(current_time_ns),
        }

    def _record_decision_capacity_context(self, req_data, sched):
        """Persist selected-GPU capacity state immediately before admission."""
        geo = req_data.get('geo')
        if geo is None:
            return
        snapshot = self._capacity_snapshot(sched, req_data)
        self._write_capacity_snapshot(geo, 'router_decision', snapshot)
        geo['router_capacity_retry_count'] = int(
            req_data.get('_capacity_retry_count', 0)
        )

    @staticmethod
    def _clear_capacity_failure(req_data):
        geo = req_data.get('geo')
        if geo is None:
            return
        for key in (
            'redirect_capacity_reason', 'capacity_running_reqs',
            'capacity_max_num_seqs', 'capacity_required_kv_bytes',
            'capacity_free_npu_bytes', 'capacity_projected_active_kv_bytes',
            'capacity_kv_budget_bytes', 'capacity_available_kv_bytes',
        ):
            geo.pop(key, None)

    def _has_capacity(self, sched, req_data, record_failure=True):
        """Return whether ``sched`` can safely reserve the complete request.

        Besides the running-sequence limit, admission reserves block-rounded
        KV for the maximum context of every waiting/inflight request and the
        candidate request. Finished, evictable prefix-cache entries are not
        reserved and may be evicted by the scheduler. This prevents a request
        accepted during prefill from exhausting NPU memory later during decode.
        """
        snapshot = self._capacity_snapshot(sched, req_data)
        running_reqs = snapshot['running_reqs']
        required_kv_bytes = snapshot['required_kv_bytes']
        free_npu_bytes = snapshot['free_npu_bytes']
        projected_active_kv_bytes = snapshot['projected_active_kv_bytes']
        kv_budget_bytes = snapshot['kv_budget_bytes']
        available_kv_bytes = snapshot['available_kv_bytes']
        slot_available = running_reqs < snapshot['max_num_seqs']
        memory_available = required_kv_bytes <= available_kv_bytes

        if slot_available and memory_available:
            return True

        if not record_failure:
            return False

        if not slot_available and not memory_available:
            reason = 'sequence_and_memory'
        elif not slot_available:
            reason = 'sequence_full'
        else:
            reason = 'npu_memory'
        geo = req_data.get('geo')
        if geo is None:
            geo = {}
            req_data['geo'] = geo
        geo.update({
            'redirect_capacity_reason': reason,
            'capacity_running_reqs': running_reqs,
            'capacity_max_num_seqs': snapshot['max_num_seqs'],
            'capacity_required_kv_bytes': required_kv_bytes,
            'capacity_free_npu_bytes': free_npu_bytes,
            'capacity_projected_active_kv_bytes': projected_active_kv_bytes,
            'capacity_kv_budget_bytes': kv_budget_bytes,
            'capacity_available_kv_bytes': available_kv_bytes,
        })
        geo.setdefault('router_first_block_reason', reason)
        geo.setdefault('router_first_block_instance_id', sched.instance_id)
        return False

    @staticmethod
    def _defer_capacity_retry(req_data, current_time_ns, source_sched, target_sched):
        """Wait for capacity when neither nearest candidate can admit safely."""
        required = int((req_data.get('geo') or {}).get('capacity_required_kv_bytes', 0))
        largest_budget = max(
            int(source_sched.memory.mem_for_kv),
            int(target_sched.memory.mem_for_kv),
        )
        if required > largest_budget:
            raise RuntimeError(
                "Request cannot fit on any redirect candidate even when idle: "
                f"required KV {required} bytes, largest KV budget {largest_budget} bytes"
            )
        req_data['arrival_time_ns'] = max(
            int(req_data['arrival_time_ns']), int(current_time_ns) + 1
        )
        req_data.setdefault('_router_capacity_wait_start_ns', int(current_time_ns))
        req_data['_capacity_retry_count'] = int(req_data.get('_capacity_retry_count', 0)) + 1
        return True

    def _maybe_reject_and_redirect(self, req_data, current_time_ns):
        """NEAREST_REJECT: check the nearest GPU's capacity; if full, charge a
        capacity-check round trip to it and redirect to the second-nearest
        GPU, which is accepted unconditionally (resolved at most once per
        request -- no cascading rejection to a third-nearest GPU).

        Modeling assumption: the capacity check itself is a lightweight
        probe (round-trip propagation delay only, no payload serialization)
        since admission here only depends on a running-slot count, not on
        the request's content. The actual accepted request to the
        second-nearest GPU pays full uplink+downlink (distance +
        serialization) like a normal request.

        Returns True if the request was deferred (arrival time pushed back
        and the caller must reinsert it into the pending queue instead of
        routing it now); False if it was accepted at the nearest GPU as-is.
        """
        geo = req_data.get('geo')
        if geo is None or 'assigned_instance_id' not in req_data:
            raise RuntimeError(
                "NEAREST_REJECT routing policy requires a geographic workload "
                "with second-nearest-GPU fields (generate it with "
                "`python -m workloads.generators geographic`)."
            )

        geo.setdefault('nearest_gpu_id', geo.get('gpu_id'))

        nearest_instance_id = req_data['assigned_instance_id']
        sched = self._find_scheduler(self.prefill_schedulers, nearest_instance_id)
        if sched is None:
            raise RuntimeError(
                f"NEAREST_REJECT routing: assigned_instance_id {nearest_instance_id} "
                f"does not match any available prefill instance."
            )

        if self._has_capacity(sched, req_data):
            self._clear_capacity_failure(req_data)
            return False

        nearest_distance_m = float(geo['distance_m'])
        second_gpu_id = int(geo['second_nearest_gpu_id'])
        target_sched = self._find_scheduler(self.prefill_schedulers, second_gpu_id)
        if target_sched is None:
            raise RuntimeError(
                f"NEAREST_REJECT routing: second_nearest_gpu_id {second_gpu_id} "
                "does not match any available prefill instance."
            )
        if not self._has_capacity(target_sched, req_data, record_failure=False):
            return self._defer_capacity_retry(
                req_data, current_time_ns, sched, target_sched
            )
        second_distance_m = float(geo['second_nearest_distance_m'])
        per_meter_ns = float(geo['distance_latency_ns_per_meter'])
        mbps = float(geo['network_throughput_mbps'])
        request_payload_bytes = float(geo['request_payload_bytes'])
        first_token_payload_bytes = float(geo['first_token_payload_bytes'])

        # SPEC: fixed APN RTT (10cell_apn spec section 6) overrides the
        # distance-proportional round trip / resend propagation when set.
        if self.apn_fixed_propagation_ns is not None:
            reject_penalty_ns = round(2 * self.apn_fixed_propagation_ns)
            uplink_distance_ns = round(self.apn_fixed_propagation_ns)
        else:
            reject_penalty_ns = round(2 * nearest_distance_m * per_meter_ns)
            uplink_distance_ns = round(second_distance_m * per_meter_ns)
        uplink_serialization_ns = round(8000.0 * request_payload_bytes / mbps)
        uplink_latency_ns = uplink_distance_ns + uplink_serialization_ns

        downlink_distance_ns = uplink_distance_ns
        downlink_serialization_ns = round(8000.0 * first_token_payload_bytes / mbps)
        downlink_latency_ns = downlink_distance_ns + downlink_serialization_ns

        geo.update({
            'gpu_id': second_gpu_id,
            'distance_m': second_distance_m,
            'uplink_distance_latency_ns': uplink_distance_ns,
            'uplink_serialization_latency_ns': uplink_serialization_ns,
            'uplink_latency_ns': uplink_latency_ns,
            'downlink_distance_latency_ns': downlink_distance_ns,
            'downlink_serialization_latency_ns': downlink_serialization_ns,
            'downlink_latency_ns': downlink_latency_ns,
            'communication_latency_ns': reject_penalty_ns + uplink_latency_ns + downlink_latency_ns,
            'rerouted': 1,
            'reject_penalty_ns': reject_penalty_ns,
        })
        req_data['assigned_instance_id'] = second_gpu_id
        req_data['arrival_time_ns'] = current_time_ns + reject_penalty_ns + uplink_latency_ns
        req_data['_reject_resolved'] = True
        return True

    def _maybe_migrate_and_redirect(self, req_data, current_time_ns):
        """NEAREST_MIGRATE: if the nearest GPU has no free running slot,
        forward the request over the GPU-to-GPU backbone link to the
        second-nearest GPU instead of queueing (resolved at most once per
        request -- no cascading migration to a third-nearest GPU).

        Unlike NEAREST_REJECT, this models a server-side redirect rather
        than a UE resend: the UE->nearest-GPU uplink already happened (it's
        baked into the geographic generator's `arrival_time_ns`), so
        detecting "no free slot" is a free, local, instant check -- no
        wasted round trip to the UE. The only new cost is the backbone hop
        (GPU_A -> GPU_B, at the operator's own bandwidth/distance, distinct
        from the UE<->GPU access link) to move the request. The eventual
        first token is delivered directly from GPU_B to the original UE
        using the normal UE<->GPU access-link model, at GPU_B's distance
        instead of GPU_A's.

        Returns True if the request was deferred (arrival time pushed back
        by the backbone transfer time and the caller must reinsert it into
        the pending queue); False if it was accepted at the nearest GPU
        as-is.
        """
        geo = req_data.get('geo')
        if geo is None or 'assigned_instance_id' not in req_data:
            raise RuntimeError(
                "NEAREST_MIGRATE routing policy requires a geographic workload "
                "with second-nearest-GPU fields (generate it with "
                "`python -m workloads.generators geographic`)."
            )
        if self.gpu_backbone_bandwidth_gbps is None:
            raise RuntimeError(
                "NEAREST_MIGRATE routing policy requires --gpu-backbone-bandwidth-gbps "
                "(GPU-to-GPU backbone link, distinct from the UE<->GPU access link)."
            )
        # SPEC: distance is only needed for the legacy distance-proportional
        # model; a fixed APN propagation constant (10cell_apn spec section 6)
        # is an accepted substitute.
        if self.gpu_backbone_distance_m is None and self.apn_fixed_propagation_ns is None:
            raise RuntimeError(
                "NEAREST_MIGRATE routing policy requires either --gpu-backbone-distance-m "
                "(distance-proportional model) or --apn-fixed-propagation-ns (fixed APN "
                "propagation model)."
            )

        geo.setdefault('nearest_gpu_id', geo.get('gpu_id'))

        nearest_instance_id = req_data['assigned_instance_id']
        sched = self._find_scheduler(self.prefill_schedulers, nearest_instance_id)
        if sched is None:
            raise RuntimeError(
                f"NEAREST_MIGRATE routing: assigned_instance_id {nearest_instance_id} "
                f"does not match any available prefill instance."
            )

        if self._has_capacity(sched, req_data):
            self._clear_capacity_failure(req_data)
            return False

        second_gpu_id = int(geo['second_nearest_gpu_id'])
        target_sched = self._find_scheduler(self.prefill_schedulers, second_gpu_id)
        if target_sched is None:
            raise RuntimeError(
                f"NEAREST_MIGRATE routing: second_nearest_gpu_id {second_gpu_id} "
                "does not match any available prefill instance."
            )
        if not self._has_capacity(target_sched, req_data, record_failure=False):
            return self._defer_capacity_retry(
                req_data, current_time_ns, sched, target_sched
            )
        second_distance_m = float(geo['second_nearest_distance_m'])
        per_meter_ns = float(geo['distance_latency_ns_per_meter'])
        access_mbps = float(geo['network_throughput_mbps'])
        request_payload_bytes = float(geo['request_payload_bytes'])
        first_token_payload_bytes = float(geo['first_token_payload_bytes'])

        # --- GPU_A -> GPU_B backbone forward (operator network, not the UE link) ---
        backbone_mbps = float(self.gpu_backbone_bandwidth_gbps) * 1000.0
        # SPEC: fixed APN propagation (10cell_apn spec section 6) overrides
        # the distance-proportional backbone/downlink propagation when set --
        # both legs are APN hops under the spec's single-network framing.
        if self.apn_fixed_propagation_ns is not None:
            migration_distance_ns = round(self.apn_fixed_propagation_ns)
        else:
            migration_distance_ns = round(float(self.gpu_backbone_distance_m) * per_meter_ns)
        migration_serialization_ns = round(8000.0 * request_payload_bytes / backbone_mbps)
        migration_latency_ns = migration_distance_ns + migration_serialization_ns

        # --- GPU_B -> UE downlink (normal access link, but from GPU_B's distance) ---
        if self.apn_fixed_propagation_ns is not None:
            downlink_distance_ns = round(self.apn_fixed_propagation_ns)
        else:
            downlink_distance_ns = round(second_distance_m * per_meter_ns)
        downlink_serialization_ns = round(8000.0 * first_token_payload_bytes / access_mbps)
        downlink_latency_ns = downlink_distance_ns + downlink_serialization_ns

        # Original UE -> GPU_A uplink already happened; keep it as paid, just
        # fold it into the total communication cost alongside the new legs.
        uplink_latency_ns = float(geo.get('uplink_latency_ns', 0))

        geo.update({
            'gpu_id': second_gpu_id,
            'distance_m': second_distance_m,
            'downlink_distance_latency_ns': downlink_distance_ns,
            'downlink_serialization_latency_ns': downlink_serialization_ns,
            'downlink_latency_ns': downlink_latency_ns,
            'communication_latency_ns': uplink_latency_ns + migration_latency_ns + downlink_latency_ns,
            'rerouted': 1,
            'migration_latency_ns': migration_latency_ns,
        })
        req_data['assigned_instance_id'] = second_gpu_id
        req_data['arrival_time_ns'] = current_time_ns + migration_latency_ns
        req_data['_reject_resolved'] = True
        if self.routing_policy == "NEAREST_MIGRATE_KV":
            self._attach_kv_handoff(
                req_data,
                source_instance_id=nearest_instance_id,
                target_instance_id=second_gpu_id,
            )
        return True

    def _attach_kv_handoff(self, req_data, source_instance_id, target_instance_id):
        """Attach dynamic KV handoff metadata for NEAREST_MIGRATE_KV.

        The regular failover path later seeds the target scheduler's prefix
        cache and charges the KV transfer latency. Keeping the metadata in the
        same shape as static failover workloads lets the scheduler and CSV
        output stay shared between both experiments.
        """
        reuse_prefix_toks = int(req_data.get('reuse_prefix_toks', 0))
        if reuse_prefix_toks <= 0:
            return

        req_data['failover'] = {
            'failover_mode': 'migrate_kv',
            'failed_instance_id': source_instance_id,
            'target_instance_id': target_instance_id,
            'reuse_prefix_toks': reuse_prefix_toks,
            'kv_migration_bandwidth_gbps': float(req_data.get(
                'kv_migration_bandwidth_gbps',
                self.gpu_backbone_bandwidth_gbps or DEFAULT_KV_MIGRATION_BANDWIDTH_GBPS,
            )),
            'kv_migration_distance_m': float(req_data.get(
                'kv_migration_distance_m',
                self.gpu_backbone_distance_m or DEFAULT_KV_MIGRATION_DISTANCE_M,
            )),
            'distance_latency_ns_per_meter': float(req_data.get(
                'distance_latency_ns_per_meter',
                DEFAULT_DISTANCE_LATENCY_NS_PER_M,
            )),
        }

    def _attach_local_kv_reuse(self, req_data, target_instance_id):
        """Attach local prefix-cache reuse metadata without transfer latency."""
        reuse_prefix_toks = int(req_data.get('reuse_prefix_toks', 0))
        if reuse_prefix_toks <= 0 or req_data.get('failover'):
            return
        req_data['failover'] = {
            'failover_mode': 'local_kv',
            'failed_instance_id': target_instance_id,
            'target_instance_id': target_instance_id,
            'reuse_prefix_toks': reuse_prefix_toks,
        }

    def _request_migration_cost_ns(self, geo):
        backbone_mbps = float(self.gpu_backbone_bandwidth_gbps) * 1000.0
        if self.apn_fixed_propagation_ns is not None:
            propagation_ns = round(self.apn_fixed_propagation_ns)
        else:
            propagation_ns = round(
                float(self.gpu_backbone_distance_m) *
                float(geo['distance_latency_ns_per_meter'])
            )
        serialization_ns = round(
            8000.0 * float(geo['request_payload_bytes']) / backbone_mbps
        )
        return propagation_ns + serialization_ns

    def _kv_migration_cost_ns(self, sched, req_data):
        reuse_tokens = min(
            int(req_data.get('reuse_prefix_toks', 0)),
            int(req_data.get('input_toks', 0)),
        )
        if reuse_tokens <= 0:
            return 0
        migration_bytes = sched.memory.get_kv(reuse_tokens) * sched.num_npus
        bandwidth_gbps = float(req_data.get(
            'kv_migration_bandwidth_gbps',
            self.gpu_backbone_bandwidth_gbps or DEFAULT_KV_MIGRATION_BANDWIDTH_GBPS,
        ))
        serialization_ns = round(8.0 * migration_bytes / bandwidth_gbps)
        if self.apn_fixed_propagation_ns is not None:
            propagation_ns = round(self.apn_fixed_propagation_ns)
        else:
            distance_m = float(req_data.get(
                'kv_migration_distance_m',
                self.gpu_backbone_distance_m or DEFAULT_KV_MIGRATION_DISTANCE_M,
            ))
            per_meter_ns = float(req_data.get(
                'distance_latency_ns_per_meter',
                DEFAULT_DISTANCE_LATENCY_NS_PER_M,
            ))
            propagation_ns = round(distance_m * per_meter_ns)
        apn_leg_ns = propagation_ns + serialization_ns
        if self.kv_staging_bandwidth_gbytes_per_s is not None and self.kv_staging_latency_ns is not None:
            staging_ns = (
                self.kv_staging_latency_ns +
                migration_bytes / self.kv_staging_bandwidth_gbytes_per_s
            )
            return round(staging_ns + apn_leg_ns + staging_ns)
        return apn_leg_ns

    def _scheduler_work_tokens(self, sched):
        work = sum(
            max(0, req.original_input - req.num_computed_tokens)
            if req.is_prefill() else 1
            for req in sched.request
        )
        for batch in sched.inflight:
            scheduled = batch.scheduled_tokens or {}
            work += sum(max(1, int(scheduled.get(req.id, req.chunk_len))) for req in batch.requests)
        work += sum(
            reservation['prefill_tokens']
            for reservation in self._adaptive_reservations.get(sched.instance_id, {}).values()
        )
        return work

    def _work_time_ns(self, sched, tokens):
        tokens = max(0, int(tokens))
        if tokens == 0:
            return 0
        budget = max(1, int(sched.max_num_batched_tokens))
        iterations = (tokens + budget - 1) // budget
        return round(
            tokens * self.adaptive_token_time_ns +
            iterations * self.adaptive_iteration_time_ns
        )

    def _capacity_release_wait_ns(self, sched, req_data):
        snapshot = self._capacity_snapshot(sched, req_data)
        if snapshot['admissible']:
            return 0
        memory_deficit = max(
            0,
            snapshot['required_kv_bytes'] - snapshot['available_kv_bytes'],
        )
        slots_needed = max(
            0,
            snapshot['running_reqs'] + snapshot['reserved_slots'] + 1 -
            snapshot['max_num_seqs'],
        )
        released_bytes = 0
        released_slots = 0
        work_tokens = 0
        candidates = sorted(
            self._active_requests(sched),
            key=lambda req: max(1, req.output - req.num_computed_tokens),
        )
        for req in candidates:
            remaining = max(1, req.output - req.num_computed_tokens)
            work_tokens += remaining
            released_bytes += self._full_request_kv_bytes(sched, req.output)
            released_slots += 1
            if released_bytes >= memory_deficit and released_slots >= slots_needed:
                break
        if released_bytes < memory_deficit or released_slots < slots_needed:
            return float('inf')
        return self._work_time_ns(sched, work_tokens)

    def _reserve_adaptive_target(self, sched, req_data, prefill_tokens):
        reservations = self._adaptive_reservations.setdefault(sched.instance_id, {})
        reservations[req_data['index']] = {
            'kv_bytes': self._full_request_kv_bytes(sched, req_data['output_toks']),
            'prefill_tokens': max(0, int(prefill_tokens)),
            'arrival_time_ns': int(req_data['arrival_time_ns']),
        }

    def _release_adaptive_reservation(self, req_data):
        target_id = req_data.get('_adaptive_reserved_instance_id')
        if target_id is None:
            return
        reservations = self._adaptive_reservations.get(target_id, {})
        reservations.pop(req_data['index'], None)
        if not reservations:
            self._adaptive_reservations.pop(target_id, None)
        req_data.pop('_adaptive_reserved_instance_id', None)

    def _maybe_adaptive_route(self, req_data, current_time_ns):
        """Choose local wait, cold migration, or KV handoff by predicted TTFT."""
        geo = req_data.get('geo')
        if geo is None or 'second_nearest_gpu_id' not in geo:
            raise RuntimeError(
                "NEAREST_SECOND_TTFT_RESERVE requires a geographic workload with second-nearest-GPU fields"
            )
        if self.gpu_backbone_bandwidth_gbps is None:
            raise RuntimeError("NEAREST_SECOND_TTFT_RESERVE requires --gpu-backbone-bandwidth-gbps")
        if self.gpu_backbone_distance_m is None and self.apn_fixed_propagation_ns is None:
            raise RuntimeError(
                "NEAREST_SECOND_TTFT_RESERVE requires --gpu-backbone-distance-m or --apn-fixed-propagation-ns"
            )

        home_id = int(req_data['assigned_instance_id'])
        target_id = int(geo['second_nearest_gpu_id'])
        home = self._find_scheduler(self.prefill_schedulers, home_id)
        target = self._find_scheduler(self.prefill_schedulers, target_id)
        if home is None or target is None:
            raise RuntimeError("NEAREST_SECOND_TTFT_RESERVE candidate does not match a prefill instance")

        input_tokens = int(req_data['input_toks'])
        reuse_tokens = min(input_tokens, int(req_data.get('reuse_prefix_toks', 0)))
        local_prefill = input_tokens - reuse_tokens
        local_wait = self._capacity_release_wait_ns(home, req_data)
        local_queue = self._work_time_ns(home, self._scheduler_work_tokens(home))
        local_response = float(geo.get('downlink_latency_ns', 0))
        t_local = (
            local_wait + local_queue + self._work_time_ns(home, local_prefill) +
            local_response
        )

        request_migration = self._request_migration_cost_ns(geo)
        target_wait = self._capacity_release_wait_ns(target, req_data)
        target_queue = self._work_time_ns(target, self._scheduler_work_tokens(target))
        second_distance_m = float(geo['second_nearest_distance_m'])
        per_meter_ns = float(geo['distance_latency_ns_per_meter'])
        if self.apn_fixed_propagation_ns is not None:
            downlink_distance_ns = round(self.apn_fixed_propagation_ns)
        else:
            downlink_distance_ns = round(second_distance_m * per_meter_ns)
        downlink_serialization_ns = round(
            8000.0 * float(geo['first_token_payload_bytes']) /
            float(geo['network_throughput_mbps'])
        )
        target_response = downlink_distance_ns + downlink_serialization_ns
        t_cold = (
            request_migration + target_wait + target_queue +
            self._work_time_ns(target, input_tokens) + target_response
        )
        kv_migration = self._kv_migration_cost_ns(target, req_data)
        t_kv = (
            request_migration + kv_migration + target_wait + target_queue +
            self._work_time_ns(target, input_tokens - reuse_tokens) + target_response
        )

        choices = [('local', t_local), ('cold_migrate', t_cold)]
        if reuse_tokens > 0 and target.enable_prefix_caching:
            choices.append(('kv_handoff', t_kv))
        mode, predicted_ns = min(choices, key=lambda item: item[1])
        geo.update({
            'adaptive_selected_route': mode,
            'adaptive_predicted_local_ttft_ns': t_local,
            'adaptive_predicted_cold_ttft_ns': t_cold,
            'adaptive_predicted_kv_ttft_ns': t_kv,
            'adaptive_predicted_selected_ttft_ns': predicted_ns,
            'adaptive_target_reserved_kv_bytes': 0,
            'adaptive_target_reserved_prefill_tokens': 0,
        })

        if mode == 'local':
            if self._has_capacity(home, req_data):
                self._clear_capacity_failure(req_data)
                return False
            return self._defer_capacity_retry(req_data, current_time_ns, home, target)

        prefill_tokens = input_tokens if mode == 'cold_migrate' else input_tokens - reuse_tokens
        req_data['arrival_time_ns'] = int(current_time_ns) + request_migration
        self._reserve_adaptive_target(target, req_data, prefill_tokens)
        req_data['_adaptive_reserved_instance_id'] = target_id
        geo.update({
            'nearest_gpu_id': geo.get('gpu_id'),
            'gpu_id': target_id,
            'distance_m': second_distance_m,
            'downlink_distance_latency_ns': downlink_distance_ns,
            'downlink_serialization_latency_ns': downlink_serialization_ns,
            'downlink_latency_ns': downlink_distance_ns + downlink_serialization_ns,
            'communication_latency_ns': (
                float(geo.get('uplink_latency_ns', 0)) + request_migration +
                downlink_distance_ns + downlink_serialization_ns
            ),
            'rerouted': 1,
            'migration_latency_ns': request_migration,
            'adaptive_target_reserved_kv_bytes': self._full_request_kv_bytes(
                target, req_data['output_toks']
            ),
            'adaptive_target_reserved_prefill_tokens': prefill_tokens,
        })
        req_data['assigned_instance_id'] = target_id
        req_data['_adaptive_resolved'] = True
        if mode == 'kv_handoff':
            self._attach_kv_handoff(req_data, home_id, target_id)
        else:
            req_data.pop('failover', None)
        return True

    def _maybe_capacity_dynamic_formula_route(self, req_data, current_time_ns):
        """Re-evaluate blocked formula routes until home or a target wins."""
        geo = req_data.get('geo')
        if geo is None or 'second_nearest_gpu_id' not in geo:
            raise RuntimeError(
                "Dynamic formula routing requires a geographic workload with "
                "second-nearest-GPU fields"
            )
        if self.gpu_backbone_bandwidth_gbps is None:
            raise RuntimeError(
                "Dynamic formula routing requires --gpu-backbone-bandwidth-gbps"
            )
        candidate_selector = MULTI_CANDIDATE_SELECTORS.get(self.routing_policy)
        multi_candidate = candidate_selector is not None
        if multi_candidate and self.apn_fixed_propagation_ns is None:
            raise RuntimeError(
                "Multi-candidate formula routing requires --apn-fixed-propagation-ns "
                "because the workload only records distance to the second-nearest GPU"
            )
        if self.gpu_backbone_distance_m is None and self.apn_fixed_propagation_ns is None:
            raise RuntimeError(
                "Dynamic formula routing requires --gpu-backbone-distance-m or "
                "--apn-fixed-propagation-ns"
            )

        selected = req_data.get('_oneshot_selected_route')
        if selected in ('cold_migrate', 'kv_handoff'):
            return False

        home_id = int(req_data.get(
            '_oneshot_home_instance_id', req_data['assigned_instance_id']
        ))
        req_data['_oneshot_home_instance_id'] = home_id
        home = self._find_scheduler(self.prefill_schedulers, home_id)
        if home is None:
            raise RuntimeError(
                f"Dynamic formula home {home_id} does not match a prefill instance"
            )

        reevaluations = int(req_data.get('_oneshot_reevaluation_count', 0))
        req_data['_oneshot_reevaluation_count'] = reevaluations + 1
        geo['oneshot_reevaluation_count'] = reevaluations
        if self._has_capacity(home, req_data):
            reason = 'home_admissible' if reevaluations == 0 else 'home_became_admissible'
            req_data['_oneshot_selected_route'] = 'local'
            geo.update({
                'oneshot_selected_route': 'local',
                'oneshot_decision_reason': reason,
                'oneshot_decision_time_ns': int(current_time_ns),
            })
            self._clear_capacity_failure(req_data)
            # SPEC: 2026-07-27_speculative_kv_transfer -- home won after all;
            # any speculative pre-transfer pinned while we were waiting is
            # wasted (never credited against a real migration).
            pinned_id = req_data.get('_speculative_kv_target_instance_id')
            if self.enable_speculative_kv_migration and pinned_id is not None:
                pin_time_ns = int(req_data.get('_speculative_kv_pin_time_ns', current_time_ns))
                geo['speculative_kv_wasted'] = 1
                geo['speculative_kv_wasted_ns'] = max(0, int(current_time_ns) - pin_time_ns)
            return False

        if (self.counterfactual_force_local_request_id is not None
                and int(req_data.get('index'))
                == int(self.counterfactual_force_local_request_id)):
            # SPEC: 2026-07-25_for_speculative_test -- skip candidate
            # scoring/redirect entirely and keep waiting at home, exactly
            # like NEAREST_KV (policy A), to measure the real home-wait
            # TTFT for a request the baseline redirected.
            geo.update({
                'oneshot_selected_route': 'undecided',
                'oneshot_decision_reason':
                    'counterfactual_force_local_awaiting_home_capacity',
            })
            return self._defer_capacity_retry(req_data, current_time_ns, home, home)

        second_id = int(geo['second_nearest_gpu_id'])
        if multi_candidate:
            candidates = [
                sched for sched in self.prefill_schedulers
                if int(sched.instance_id) != home_id
            ]
            candidates.sort(key=lambda sched: (
                int(sched.instance_id) != second_id, int(sched.instance_id)
            ))
        else:
            target = self._find_scheduler(self.prefill_schedulers, second_id)
            if target is None:
                raise RuntimeError(
                    f"Dynamic formula target {second_id} does not match a prefill instance"
                )
            candidates = [target]

        admissible = [
            sched for sched in candidates
            if self._has_capacity(sched, req_data, record_failure=False)
        ]
        geo['oneshot_candidate_count'] = len(candidates)
        geo['oneshot_admissible_candidate_count'] = len(admissible)
        if not admissible:
            geo.update({
                'oneshot_selected_route': 'undecided',
                'oneshot_decision_reason': 'awaiting_candidate_capacity',
                'oneshot_target_admissible': 0,
            })
            largest = max(
                candidates, key=lambda sched: int(sched.memory.mem_for_kv)
            )
            return self._defer_capacity_retry(
                req_data, current_time_ns, home, largest
            )

        input_tokens = int(req_data['input_toks'])
        reuse_tokens = min(
            input_tokens, int(req_data.get('reuse_prefix_toks', 0))
        )
        target_prefill = input_tokens - reuse_tokens if reuse_tokens > 0 else input_tokens
        request_migration = self._request_migration_cost_ns(geo)
        downlink_distance_ns = round(
            self.apn_fixed_propagation_ns
            if self.apn_fixed_propagation_ns is not None
            else float(geo['second_nearest_distance_m'])
            * float(geo['distance_latency_ns_per_meter'])
        )
        downlink_serialization_ns = round(
            8000.0 * float(geo['first_token_payload_bytes'])
            / float(geo['network_throughput_mbps'])
        )

        no_model = candidate_selector == 'min_pressure_no_model'
        if no_model:
            local_prediction = None
            local_wait = float('nan')
            local_total = float('nan')
        else:
            local_prediction = self.ttft_formula.predict(
                self._ttft_formula_features(req_data, home), 'NEAREST_KV'
            )
            if self.enable_formula_local_wait_point_estimate:
                # SPEC: 2026-07-27_speculative_kv_transfer -- route_upper_ms
                # bakes in a fixed ~90th-percentile safety residual
                # (route_positive_ms + residual_ms, residual_ms alone is
                # ~9,700-10,300ms) that structurally always exceeds
                # oneshot_max_local_wait_ns (1,000ms default), so
                # deadline_exceeded below fires unconditionally on the very
                # first tick regardless of actual conditions -- confirmed
                # empirically (0/300 requests in
                # experiments/2026-07-21-add_gpu_utilization ever entered the
                # wait/retry loop). This flag makes local's estimate use the
                # same plain point estimate (ttft_ms) that redirect
                # candidates are already scored with below (redirect_total),
                # instead of the inflated upper bound, so home vs. redirect
                # is compared symmetrically and a genuine multi-tick wait
                # becomes possible when local's point estimate is actually
                # within budget.
                local_wait = local_prediction['ttft_ms'] * 1e6
                local_total = local_wait + float(geo.get('downlink_latency_ns', 0))
            else:
                local_wait = local_prediction['route_upper_ms'] * 1e6
                local_total = (
                    local_prediction['ttft_ms']
                    - local_prediction['route_ms']
                    + local_prediction['route_upper_ms']
                ) * 1e6 + float(geo.get('downlink_latency_ns', 0))

        scored = []
        for target in admissible:
            snapshot = self._capacity_snapshot(target, req_data)
            features = None if no_model else self._ttft_formula_features(
                req_data, target
            )
            prediction = None if no_model else self.ttft_formula.predict(
                features, 'NEAREST_MIGRATE_KV'
            )
            kv_migration = (
                self._kv_migration_cost_ns(target, req_data)
                if reuse_tokens > 0 else 0
            )
            total = (
                request_migration + kv_migration
                + downlink_distance_ns + downlink_serialization_ns
                if no_model else
                prediction['ttft_ms'] * 1e6
                + request_migration + kv_migration
                + downlink_distance_ns + downlink_serialization_ns
            )
            scored.append({
                'total': total,
                'target_id': int(target.instance_id),
                'target': target,
                'prediction': prediction,
                'features': features,
                'kv_migration': kv_migration,
                'snapshot': snapshot,
            })
        proactive_pin = self._proactive_pin_candidate(req_data, scored, reuse_tokens)
        if proactive_pin is not None:
            selected_target = proactive_pin
            geo['proactive_kv_prewarm_pin_applied'] = 1
        elif candidate_selector == 'min_waiting':
            selected_target = min(
                scored,
                key=lambda item: (
                    item['snapshot']['waiting_reqs'], item['target_id']
                ),
            )
        elif candidate_selector in ('min_pressure', 'min_pressure_no_model'):
            selected_target = min(
                scored,
                key=lambda item: (
                    item['snapshot']['capacity_pressure'], item['target_id']
                ),
            )
        elif candidate_selector == 'random':
            selected_target = self._rnd.choice(scored)
        else:
            selected_target = min(
                scored, key=lambda item: (item['total'], item['target_id'])
            )
        model_selected_target = selected_target
        counterfactual_override = (
            self.counterfactual_request_id is not None
            and int(req_data.get('index')) == int(self.counterfactual_request_id)
        )
        if counterfactual_override:
            forced = [
                item for item in scored
                if item['target_id'] == int(self.counterfactual_target_instance_id)
            ]
            if not forced:
                raise RuntimeError(
                    "Counterfactual target is not admissible at the routing decision: "
                    f"request={self.counterfactual_request_id}, "
                    f"target={self.counterfactual_target_instance_id}"
                )
            selected_target = forced[0]
        redirect_total = selected_target['total']
        target_id = selected_target['target_id']
        target = selected_target['target']
        redirect_prediction = selected_target['prediction']
        kv_migration = selected_target['kv_migration']

        if not no_model:
            pressure_order = sorted(
                scored,
                key=lambda item: (
                    item['snapshot']['capacity_pressure'], item['target_id']
                ),
            )
            model_order = sorted(
                scored, key=lambda item: (item['total'], item['target_id'])
            )
            pressure_rank = {
                item['target_id']: rank
                for rank, item in enumerate(pressure_order, start=1)
            }
            model_rank = {
                item['target_id']: rank
                for rank, item in enumerate(model_order, start=1)
            }
            for item in scored:
                prediction = item['prediction']
                row = {
                    'request_id': req_data.get('index'),
                    'decision_time_ns': int(current_time_ns),
                    'reevaluation_count': reevaluations,
                    'home_instance_id': home_id,
                    'candidate_instance_id': item['target_id'],
                    'candidate_count': len(candidates),
                    'admissible_candidate_count': len(admissible),
                    'capacity_pressure_rank': pressure_rank[item['target_id']],
                    'model_ttft_rank': model_rank[item['target_id']],
                    'selected_by_model': int(item is model_selected_target),
                    'selected_for_routing': int(item is selected_target),
                    'counterfactual_override': int(counterfactual_override),
                    'predicted_total_ttft_ms': item['total'] / 1e6,
                    'predicted_route_probability': prediction['route_probability'],
                    'predicted_route_positive_ms': prediction['route_positive_ms'],
                    'predicted_route_upper_ms': prediction['route_upper_ms'],
                    'predicted_route_ms': prediction['route_ms'],
                    'predicted_scheduler_raw_ms': prediction.get(
                        'scheduler_raw_ms', prediction['scheduler_ms']
                    ),
                    'predicted_scheduler_ms': prediction['scheduler_ms'],
                    'predicted_compute_ms': prediction['compute_ms'],
                    'predicted_formula_ttft_ms': prediction['ttft_ms'],
                    'request_migration_ms': request_migration / 1e6,
                    'kv_migration_ms': item['kv_migration'] / 1e6,
                    'downlink_ms': (
                        downlink_distance_ns + downlink_serialization_ns
                    ) / 1e6,
                }
                row.update({
                    f'candidate_{key}': value
                    for key, value in item['snapshot'].items()
                })
                row.update({
                    f'feature_{key}': value
                    for key, value in item['features'].items()
                })
                self.candidate_diagnostics.append(row)

        wait_start = req_data.get('_router_capacity_wait_start_ns')
        elapsed_wait = (
            max(0, int(current_time_ns) - int(wait_start))
            if wait_start is not None else 0
        )
        margin_wins = no_model or (
            redirect_total + self.oneshot_redirect_margin_ns < local_total
        )
        deadline_exceeded = no_model or (
            local_wait > self.oneshot_max_local_wait_ns
            or elapsed_wait > self.oneshot_max_local_wait_ns
        )

        geo.update({
            'oneshot_prediction_model': (
                'none' if no_model else 'offline_ttft_formula_dynamic'
            ),
            'oneshot_candidate_selector': candidate_selector or 'second_nearest',
            'oneshot_decision_time_ns': int(current_time_ns),
            'oneshot_predicted_local_wait_ns': local_wait,
            'oneshot_predicted_local_ttft_ns': local_total,
            'oneshot_predicted_redirect_ttft_ns': redirect_total,
            'oneshot_redirect_margin_ns': self.oneshot_redirect_margin_ns,
            'oneshot_max_local_wait_ns': self.oneshot_max_local_wait_ns,
            'oneshot_target_admissible': 1,
            'oneshot_target_reservation_enabled': int(
                self.enable_oneshot_target_reservation
            ),
            'oneshot_selected_target_instance_id': target_id,
        })
        if not no_model:
            geo.update({
                'oneshot_formula_local_route_probability': local_prediction['route_probability'],
                'oneshot_formula_local_route_positive_ms': local_prediction['route_positive_ms'],
                'oneshot_formula_local_route_ms': local_prediction['route_ms'],
                'oneshot_formula_local_scheduler_ms': local_prediction['scheduler_ms'],
                'oneshot_formula_local_compute_ms': local_prediction['compute_ms'],
                'oneshot_formula_redirect_route_probability': redirect_prediction['route_probability'],
                'oneshot_formula_redirect_route_positive_ms': redirect_prediction['route_positive_ms'],
                'oneshot_formula_redirect_route_ms': redirect_prediction['route_ms'],
                'oneshot_formula_redirect_scheduler_ms': redirect_prediction['scheduler_ms'],
                'oneshot_formula_redirect_compute_ms': redirect_prediction['compute_ms'],
            })

        if not margin_wins and not deadline_exceeded:
            # SPEC: 2026-07-27_speculative_kv_transfer -- Method B: pin the
            # current best-scored candidate once, the first time this
            # request is left waiting (never re-pinned to a different
            # candidate on later re-evaluations, even if the model's ranking
            # changes -- see MODEL_ITERATION_HISTORY.md on how unstable
            # candidate rankings are between close candidates). The pin is a
            # side channel only; margin_wins/deadline_exceeded/scored keep
            # being recomputed fresh every call exactly as before.
            if (self.enable_speculative_kv_migration
                    and '_speculative_kv_target_instance_id' not in req_data):
                req_data['_speculative_kv_target_instance_id'] = target_id
                req_data['_speculative_kv_pin_time_ns'] = int(current_time_ns)
                geo['speculative_kv_target_instance_id'] = target_id
                geo['speculative_kv_pinned_at_ns'] = int(current_time_ns)
            geo.update({
                'oneshot_selected_route': 'undecided',
                'oneshot_decision_reason': 'awaiting_predicted_local',
            })
            return self._defer_capacity_retry(
                req_data, current_time_ns, home, target
            )

        mode = (
            'kv_handoff'
            if reuse_tokens > 0 and target.enable_prefix_caching
            else 'cold_migrate'
        )
        req_data['_oneshot_selected_route'] = mode
        geo['oneshot_selected_route'] = mode
        geo['oneshot_decision_reason'] = (
            'home_not_admissible_min_pressure'
            if no_model else
            'elapsed_local_wait_exceeds_limit'
            if elapsed_wait > self.oneshot_max_local_wait_ns
            else 'predicted_local_wait_exceeds_limit'
            if deadline_exceeded
            else 'redirect_beats_local_by_margin'
        )
        req_data['arrival_time_ns'] = int(current_time_ns) + request_migration
        if self.enable_oneshot_target_reservation:
            self._reserve_adaptive_target(target, req_data, target_prefill)
            req_data['_adaptive_reserved_instance_id'] = target_id
        req_data['assigned_instance_id'] = target_id
        geo.update({
            'nearest_gpu_id': geo.get('gpu_id'),
            'gpu_id': target_id,
            'distance_m': float(geo['second_nearest_distance_m']),
            'downlink_distance_latency_ns': downlink_distance_ns,
            'downlink_serialization_latency_ns': downlink_serialization_ns,
            'downlink_latency_ns': downlink_distance_ns + downlink_serialization_ns,
            'communication_latency_ns': (
                float(geo.get('uplink_latency_ns', 0)) + request_migration
                + downlink_distance_ns + downlink_serialization_ns
            ),
            'rerouted': 1,
            'migration_latency_ns': request_migration,
        })
        # SPEC: 2026-07-27_speculative_kv_transfer -- Method B: credit
        # elapsed pinned-speculation time only if we ended up actually
        # migrating KV to the exact GPU we speculated to; otherwise the
        # speculative transfer (if any) was wasted.
        pinned_id = req_data.get('_speculative_kv_target_instance_id')
        if self.enable_speculative_kv_migration and pinned_id is not None:
            pin_time_ns = int(req_data.get('_speculative_kv_pin_time_ns', current_time_ns))
            if mode == 'kv_handoff' and pinned_id == target_id:
                req_data['_speculative_kv_elapsed_ns'] = max(
                    0, int(current_time_ns) - pin_time_ns
                )
                geo['speculative_kv_wasted'] = 0
            else:
                req_data['_speculative_kv_elapsed_ns'] = 0
                geo['speculative_kv_wasted'] = 1
                geo['speculative_kv_wasted_ns'] = max(0, int(current_time_ns) - pin_time_ns)
            geo['speculative_kv_elapsed_ns'] = req_data.get('_speculative_kv_elapsed_ns', 0)
        if mode == 'kv_handoff':
            self._attach_kv_handoff(req_data, home_id, target_id)
        else:
            req_data.pop('failover', None)
        return True

    def _ttft_formula_features(self, req_data, candidate_sched):
        candidate = self._capacity_snapshot(candidate_sched, req_data)
        snapshots = [
            self._capacity_snapshot(sched, req_data)
            for sched in self.prefill_schedulers
        ]
        workload = req_data.get('_ttft_formula_workload_features')
        if workload is None:
            raise RuntimeError(
                "TTFT formula workload features were not initialized for this request"
            )
        features = dict(workload)
        candidate_workload = req_data.get(
            '_ttft_formula_candidate_features', {}
        ).get(candidate_sched.instance_id)
        if candidate_workload is not None:
            features.update(candidate_workload)
        features.update({
            'input_tokens': int(req_data['input_toks']),
            'output_tokens': max(
                0, int(req_data['output_toks']) - int(req_data['input_toks'])
            ),
            'home_cached_prefix_tokens': min(
                int(req_data['input_toks']), int(req_data.get('reuse_prefix_toks', 0))
            ),
            'router_initial_waiting_reqs': candidate['waiting_reqs'],
            'router_initial_running_reqs': candidate['running_reqs'],
            'router_initial_required_kv_bytes': candidate['required_kv_bytes'],
            'router_initial_available_kv_bytes': candidate['available_kv_bytes'],
            'router_initial_projected_active_kv_bytes': candidate['projected_active_kv_bytes'],
            'router_initial_capacity_pressure': candidate['capacity_pressure'],
            'router_initial_slot_pressure': candidate['slot_pressure'],
            'router_initial_admissible_candidate_count': sum(
                snapshot['admissible'] for snapshot in snapshots
            ),
            'router_initial_total_waiting_reqs': sum(
                snapshot['waiting_reqs'] for snapshot in snapshots
            ),
            'router_initial_max_waiting_reqs': max(
                snapshot['waiting_reqs'] for snapshot in snapshots
            ),
            'router_initial_total_running_reqs': sum(
                snapshot['running_reqs'] for snapshot in snapshots
            ),
            'router_initial_max_running_reqs': max(
                snapshot['running_reqs'] for snapshot in snapshots
            ),
            'router_initial_min_available_kv_bytes': min(
                snapshot['available_kv_bytes'] for snapshot in snapshots
            ),
            'router_initial_max_available_kv_bytes': max(
                snapshot['available_kv_bytes'] for snapshot in snapshots
            ),
            'router_initial_min_capacity_pressure': min(
                snapshot['capacity_pressure'] for snapshot in snapshots
            ),
            'router_initial_max_capacity_pressure': max(
                snapshot['capacity_pressure'] for snapshot in snapshots
            ),
        })
        return features

    def _maybe_capacity_oneshot_route(self, req_data, current_time_ns):
        """Make one capacity-gated local-or-redirect decision at first arrival."""
        geo = req_data.get('geo')
        if geo is None or 'second_nearest_gpu_id' not in geo:
            raise RuntimeError(
                "NEAREST_CAPACITY_ONESHOT_KV_RESERVE requires a geographic workload "
                "with second-nearest-GPU fields"
            )
        if self.gpu_backbone_bandwidth_gbps is None:
            raise RuntimeError(
                "NEAREST_CAPACITY_ONESHOT_KV_RESERVE requires --gpu-backbone-bandwidth-gbps"
            )
        if self.gpu_backbone_distance_m is None and self.apn_fixed_propagation_ns is None:
            raise RuntimeError(
                "NEAREST_CAPACITY_ONESHOT_KV_RESERVE requires --gpu-backbone-distance-m "
                "or --apn-fixed-propagation-ns"
            )

        home_id = int(req_data.get('_oneshot_home_instance_id', req_data['assigned_instance_id']))
        target_id = int(geo['second_nearest_gpu_id'])
        home = self._find_scheduler(self.prefill_schedulers, home_id)
        target = self._find_scheduler(self.prefill_schedulers, target_id)
        if home is None or target is None:
            raise RuntimeError(
                "NEAREST_CAPACITY_ONESHOT_KV_RESERVE candidate does not match a prefill instance"
            )

        selected = req_data.get('_oneshot_selected_route')
        if selected == 'local':
            if self._has_capacity(home, req_data):
                self._clear_capacity_failure(req_data)
                return False
            return self._defer_capacity_retry(req_data, current_time_ns, home, target)
        if selected in ('cold_migrate', 'kv_handoff'):
            return False

        req_data['_oneshot_home_instance_id'] = home_id
        if self._has_capacity(home, req_data):
            req_data['_oneshot_selected_route'] = 'local'
            geo.update({
                'oneshot_selected_route': 'local',
                'oneshot_decision_reason': 'home_admissible',
                'oneshot_decision_time_ns': int(current_time_ns),
            })
            self._clear_capacity_failure(req_data)
            return False

        input_tokens = int(req_data['input_toks'])
        reuse_tokens = min(input_tokens, int(req_data.get('reuse_prefix_toks', 0)))
        target_snapshot = self._capacity_snapshot(target, req_data)
        target_admissible = bool(target_snapshot['admissible'])
        request_migration = self._request_migration_cost_ns(geo)
        kv_migration = self._kv_migration_cost_ns(target, req_data) if reuse_tokens > 0 else 0
        target_prefill = input_tokens - reuse_tokens if reuse_tokens > 0 else input_tokens
        second_distance_m = float(geo['second_nearest_distance_m'])
        per_meter_ns = float(geo['distance_latency_ns_per_meter'])
        downlink_distance_ns = round(
            self.apn_fixed_propagation_ns
            if self.apn_fixed_propagation_ns is not None
            else second_distance_m * per_meter_ns
        )
        downlink_serialization_ns = round(
            8000.0 * float(geo['first_token_payload_bytes']) /
            float(geo['network_throughput_mbps'])
        )
        if self.ttft_formula is not None:
            local_features = self._ttft_formula_features(req_data, home)
            target_features = self._ttft_formula_features(req_data, target)
            local_prediction = self.ttft_formula.predict(
                local_features, 'NEAREST_KV'
            )
            redirect_prediction = self.ttft_formula.predict(
                target_features, 'NEAREST_MIGRATE_KV'
            )
            # This branch is reached only after observing that home is not
            # admissible, so a probability-weighted mean is unsafe for a hard
            # wait deadline. Use the scenario-held-out upper prediction for
            # the routing decision while retaining the point estimate in the
            # diagnostic component fields below.
            local_wait = local_prediction['route_upper_ms'] * 1e6
            local_total = (
                (
                    local_prediction['ttft_ms']
                    - local_prediction['route_ms']
                    + local_prediction['route_upper_ms']
                ) * 1e6
                + float(geo.get('downlink_latency_ns', 0))
            )
            redirect_total = (
                redirect_prediction['ttft_ms'] * 1e6
                + request_migration + kv_migration
                + downlink_distance_ns + downlink_serialization_ns
            )
            geo.update({
                'oneshot_prediction_model': 'offline_ttft_formula',
                'oneshot_formula_local_route_probability': local_prediction['route_probability'],
                'oneshot_formula_local_route_positive_ms': local_prediction['route_positive_ms'],
                'oneshot_formula_local_route_ms': local_prediction['route_ms'],
                'oneshot_formula_local_scheduler_ms': local_prediction['scheduler_ms'],
                'oneshot_formula_local_compute_ms': local_prediction['compute_ms'],
                'oneshot_formula_redirect_route_probability': redirect_prediction['route_probability'],
                'oneshot_formula_redirect_route_positive_ms': redirect_prediction['route_positive_ms'],
                'oneshot_formula_redirect_route_ms': redirect_prediction['route_ms'],
                'oneshot_formula_redirect_scheduler_ms': redirect_prediction['scheduler_ms'],
                'oneshot_formula_redirect_compute_ms': redirect_prediction['compute_ms'],
            })
        else:
            geo['oneshot_prediction_model'] = 'token_time_heuristic'
            local_wait = self._capacity_release_wait_ns(home, req_data)
            local_total = (
                local_wait + self._work_time_ns(home, input_tokens - reuse_tokens) +
                float(geo.get('downlink_latency_ns', 0))
            )
            target_queue = self._work_time_ns(target, self._scheduler_work_tokens(target))
            redirect_total = (
                request_migration + kv_migration + target_queue +
                self._work_time_ns(target, target_prefill) +
                downlink_distance_ns + downlink_serialization_ns
            )
        margin_wins = redirect_total + self.oneshot_redirect_margin_ns < local_total
        deadline_exceeded = local_wait > self.oneshot_max_local_wait_ns
        should_redirect = target_admissible and (margin_wins or deadline_exceeded)

        geo.update({
            'oneshot_decision_time_ns': int(current_time_ns),
            'oneshot_predicted_local_wait_ns': local_wait,
            'oneshot_predicted_local_ttft_ns': local_total,
            'oneshot_predicted_redirect_ttft_ns': redirect_total,
            'oneshot_redirect_margin_ns': self.oneshot_redirect_margin_ns,
            'oneshot_max_local_wait_ns': self.oneshot_max_local_wait_ns,
            'oneshot_target_admissible': int(target_admissible),
            'oneshot_target_reservation_enabled': int(
                self.enable_oneshot_target_reservation
            ),
        })

        if not should_redirect:
            if not target_admissible:
                reason = 'target_not_admissible'
            elif not margin_wins and not deadline_exceeded:
                reason = 'local_within_margin_and_deadline'
            else:
                reason = 'local_selected'
            req_data['_oneshot_selected_route'] = 'local'
            geo['oneshot_selected_route'] = 'local'
            geo['oneshot_decision_reason'] = reason
            return self._defer_capacity_retry(req_data, current_time_ns, home, target)

        mode = 'kv_handoff' if reuse_tokens > 0 and target.enable_prefix_caching else 'cold_migrate'
        req_data['_oneshot_selected_route'] = mode
        geo['oneshot_selected_route'] = mode
        geo['oneshot_decision_reason'] = (
            'predicted_local_wait_exceeds_limit' if deadline_exceeded
            else 'redirect_beats_local_by_margin'
        )
        req_data['arrival_time_ns'] = int(current_time_ns) + request_migration
        if self.enable_oneshot_target_reservation:
            self._reserve_adaptive_target(target, req_data, target_prefill)
            req_data['_adaptive_reserved_instance_id'] = target_id
        req_data['assigned_instance_id'] = target_id
        geo.update({
            'nearest_gpu_id': geo.get('gpu_id'),
            'gpu_id': target_id,
            'distance_m': second_distance_m,
            'downlink_distance_latency_ns': downlink_distance_ns,
            'downlink_serialization_latency_ns': downlink_serialization_ns,
            'downlink_latency_ns': downlink_distance_ns + downlink_serialization_ns,
            'communication_latency_ns': (
                float(geo.get('uplink_latency_ns', 0)) + request_migration +
                downlink_distance_ns + downlink_serialization_ns
            ),
            'rerouted': 1,
            'migration_latency_ns': request_migration,
        })
        if mode == 'kv_handoff':
            self._attach_kv_handoff(req_data, home_id, target_id)
        else:
            req_data.pop('failover', None)
        return True
    # <<< SPEC: redirect-on-capacity routing (helpers) -------------------------

    # -----------------------------------------------------------------------
    # Request loading and real-time routing
    # -----------------------------------------------------------------------

    def _annotate_ttft_formula_features(self):
        """Precompute send-time workload features used by the offline formula."""
        if self.ttft_formula is None or not self._pending_requests:
            return
        records = []
        for req_data in self._pending_requests:
            geo = req_data.get('geo') or {}
            if 'assigned_instance_id' not in req_data:
                continue
            send_time = int(geo.get('request_send_time_ns', req_data['arrival_time_ns']))
            records.append((send_time, int(req_data['assigned_instance_id']), req_data))
        if not records:
            return
        records.sort(key=lambda item: item[0])
        first_send = records[0][0]
        duration_s = max((records[-1][0] - first_send) / 1e9, 1e-9)
        request_rate = (len(records) - 1) / duration_s
        home_counts = {}
        for _, home_id, _ in records:
            home_counts[home_id] = home_counts.get(home_id, 0) + 1

        previous_send = first_send
        for position, (send_time, home_id, req_data) in enumerate(records):
            prior = records[:position]
            req_data['_ttft_formula_workload_features'] = {
                'request_rate_rps': request_rate,
                'arrival_offset_s': (send_time - first_send) / 1e9,
                'interarrival_ms': (send_time - previous_send) / 1e6,
                'global_arrivals_1s': sum(
                    prior_send >= send_time - 1_000_000_000
                    for prior_send, _, _ in prior
                ),
                'global_arrivals_5s': sum(
                    prior_send >= send_time - 5_000_000_000
                    for prior_send, _, _ in prior
                ),
                'home_arrivals_1s': sum(
                    prior_send >= send_time - 1_000_000_000 and prior_home == home_id
                    for prior_send, prior_home, _ in prior
                ),
                'home_arrivals_5s': sum(
                    prior_send >= send_time - 5_000_000_000 and prior_home == home_id
                    for prior_send, prior_home, _ in prior
                ),
                'home_workload_share': home_counts[home_id] / len(records),
            }
            req_data['_ttft_formula_candidate_features'] = {
                candidate_id: {
                    'home_arrivals_1s': sum(
                        prior_send >= send_time - 1_000_000_000
                        and prior_home == candidate_id
                        for prior_send, prior_home, _ in prior
                    ),
                    'home_arrivals_5s': sum(
                        prior_send >= send_time - 5_000_000_000
                        and prior_home == candidate_id
                        for prior_send, prior_home, _ in prior
                    ),
                    'home_workload_share': home_counts.get(candidate_id, 0) / len(records),
                }
                for candidate_id in home_counts
            }
            previous_send = send_time

    def load_requests(self, path, enable_prefix_caching=False, is_init=True):
        """Load requests from dataset into pending queue (not yet routed).

        Supports two JSONL formats:
        - Flat: {"input_toks", "output_toks", "arrival_time_ns", ...}
        - Agentic session: {"session_id", "arrival_time_ns", "sub_requests": [...]}

        For agentic sessions, only the first sub-request is added to the
        pending queue. Subsequent sub-requests are released dynamically
        via notify_request_completed() when predecessors finish.
        """
        path = f'../{path}'
        self._enable_prefix_caching = enable_prefix_caching
        self._is_init = is_init
        loaded_lines = 0

        with open(path) as f:
            for line in f:
                if self.req_num > 0 and loaded_lines >= self.req_num:
                    break
                row = json.loads(line)
                if 'sub_requests' in row:
                    self._load_agentic_session(row, enable_prefix_caching)
                else:
                    self._load_flat_request(row, enable_prefix_caching)
                loaded_lines += 1

        # Sort pending requests by arrival time (agentic first sub-requests
        # may interleave with flat requests)
        self._pending_requests.sort(key=lambda r: r['arrival_time_ns'])
        self._annotate_ttft_formula_features()

        self.logger.info("Loaded %d requests into pending queue "
                         "(%d agentic sessions deferred)",
                         len(self._pending_requests),
                         len(self._deferred_sessions))

    def _load_flat_request(self, row, enable_prefix_caching):
        """Load a single flat request into pending queue."""
        req_id = self._next_request_id
        self._next_request_id += 1
        failover = self._extract_failover_fields(row)
        req_data = {
            'index': req_id,
            'input_toks': int(row['input_toks']),
            'output_toks': int(row['input_toks'] + row['output_toks']),
            'arrival_time_ns': int(row['arrival_time_ns']),
        }
        # >>> SPEC: fair-comparison KV baseline. Base spec gated all of
        # reuse_prefix_toks / input_hash_ids / local-KV-reuse loading to
        # NEAREST_KV and NEAREST_MIGRATE_KV only, which meant NEAREST_REJECT
        # and NEAREST_MIGRATE always paid a cold prefill for every request
        # (redirected or not) while NEAREST_KV/NEAREST_MIGRATE_KV got a free
        # local cache hit for every request that never redirects. That mixes
        # "does this policy redirect load" with "does this policy start with
        # a warm KV cache", which are independent questions. All 4 policies
        # now assume the same starting condition (every request's prefix is
        # already cached on its *home* GPU); only whether a redirected
        # request's cache follows it to the new GPU differs by policy.
        if self.routing_policy in (
            "NEAREST_KV", "NEAREST_MIGRATE_KV", "NEAREST_REJECT",
            "NEAREST_MIGRATE", "NEAREST_SECOND_TTFT_RESERVE",
            "NEAREST_CAPACITY_ONESHOT_KV_RESERVE",
            "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE",
            "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE",
            *MULTI_CANDIDATE_SELECTORS,
        ):
            req_data['reuse_prefix_toks'] = int(row.get('reuse_prefix_toks', 0))
        # <<< SPEC: fair-comparison KV baseline
        if enable_prefix_caching:
            input_hash_ids = row.get('input_tok_ids', [])
            if (
                (
                    failover and failover.get('failover_mode') == 'migrate_kv'
                ) or (
                    self.routing_policy in (
                        "NEAREST_KV", "NEAREST_MIGRATE_KV", "NEAREST_REJECT",
                        "NEAREST_MIGRATE", "NEAREST_SECOND_TTFT_RESERVE",
                        "NEAREST_CAPACITY_ONESHOT_KV_RESERVE",
                        "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE",
                        "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE",
                        *MULTI_CANDIDATE_SELECTORS,
                    )
                    and int(row.get('reuse_prefix_toks', 0)) > 0
                )
            ) and not input_hash_ids:
                input_hash_ids = list(range(int(row['input_toks'])))
            req_data['input_hash_ids'] = input_hash_ids
            req_data['output_hash_ids'] = row.get('output_tok_ids', [])
        if 'assigned_instance_id' in row:
            req_data['assigned_instance_id'] = int(row['assigned_instance_id'])
        if failover:
            req_data['failover'] = failover
        if self.routing_policy in (
            "NEAREST_KV", "NEAREST_MIGRATE_KV", "NEAREST_SECOND_TTFT_RESERVE",
            "NEAREST_CAPACITY_ONESHOT_KV_RESERVE",
            "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE",
            "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE",
            *MULTI_CANDIDATE_SELECTORS,
        ):
            if 'kv_migration_bandwidth_gbps' in row:
                req_data['kv_migration_bandwidth_gbps'] = float(row['kv_migration_bandwidth_gbps'])
            if 'kv_migration_distance_m' in row:
                req_data['kv_migration_distance_m'] = float(row['kv_migration_distance_m'])
            if 'distance_latency_ns_per_meter' in row:
                req_data['distance_latency_ns_per_meter'] = float(row['distance_latency_ns_per_meter'])
        geo = _extract_geo_fields(row)
        if geo is not None:
            req_data['geo'] = geo
        self._pending_requests.append(req_data)

    def _extract_failover_fields(self, row):
        mode = row.get('failover_mode')
        if mode is None:
            return None
        mode = str(mode).lower()
        if mode not in ('cold', 'migrate_kv', 'local_kv'):
            raise ValueError(f"Unknown failover_mode '{mode}'. Supported: cold, migrate_kv, local_kv")
        target = row.get('target_instance_id', row.get('failover_target_instance_id'))
        if target is None and mode != 'local_kv':
            raise ValueError("failover workloads require target_instance_id")
        failover = {
            'failover_mode': mode,
            'target_instance_id': int(target) if target is not None else '',
            'failed_instance_id': row.get('failed_instance_id', row.get('source_instance_id', '')),
            'reuse_prefix_toks': int(row.get('reuse_prefix_toks', 0)),
            'kv_migration_bandwidth_gbps': float(row.get(
                'kv_migration_bandwidth_gbps',
                DEFAULT_KV_MIGRATION_BANDWIDTH_GBPS,
            )),
            'kv_migration_distance_m': float(row.get(
                'kv_migration_distance_m',
                DEFAULT_KV_MIGRATION_DISTANCE_M,
            )),
            'distance_latency_ns_per_meter': float(row.get(
                'distance_latency_ns_per_meter',
                DEFAULT_DISTANCE_LATENCY_NS_PER_M,
            )),
        }
        return failover

    def _proactive_pin_candidate(self, req_data, scored, reuse_tokens):
        """Method C routing bind: if this user has an outstanding proactive
        pre-warm, force the redirect decision to land on that same target
        instead of letting the normal candidate selector (min_pressure/
        min_waiting/model) pick independently based on cluster state at
        decision time, which can drift from the cluster state the pre-warm
        was chosen under. This is what turns a correct "who will come
        back" prediction into a correct "where they land" outcome -- see
        proactive_kv_prewarm_design.md.

        Only binds when it can actually help: the pinned target must
        still be in `scored` (i.e. still admissible right now -- it may
        have filled up since the pre-warm fired) and its seeded content
        must still be resident (checked directly against the live cache,
        not the remembered seed count, since it may have been evicted by
        unrelated real traffic in the meantime). Falls back to None
        (normal selection) otherwise, so a stale/evicted pin never forces
        a worse routing decision than the request would have gotten
        anyway."""
        if not self.enable_proactive_kv_prewarm or reuse_tokens <= 0:
            return None
        geo = req_data.get('geo')
        user_id = geo.get('user_id') if geo else None
        if user_id is None:
            return None
        pending = self._proactive_migrations.get(user_id)
        if pending is None:
            return None
        item = next(
            (s for s in scored if s['target_id'] == pending['target_instance_id']),
            None,
        )
        if item is None:
            return None
        input_hash_ids = req_data.get('input_hash_ids') or list(
            range(int(req_data.get('input_toks', 0)))
        )
        match = item['target'].memory.npu_prefix_cache.match_prefix(
            input_hash_ids[:reuse_tokens]
        )
        if match.hit_length <= 0:
            return None
        return item

    def _resolve_proactive_kv_prewarm(self, req_data, sched):
        """Method C payoff Hook 1: called for EVERY routed request (not
        just migrate_kv ones), right after the final target sched is known.
        Pops any outstanding pre-warm entry for this user -- if the router's
        independent redirect decision landed on the same instance we
        pre-warmed, stash the seeded token count for Hook 2 (in
        _apply_kv_migration_if_needed) to credit; otherwise it's a wasted
        (free) pre-warm. Must run for every request so a pending entry
        always gets resolved/expired, not just carried forward and
        mis-credited to a later, unrelated request from the same user."""
        if not self.enable_proactive_kv_prewarm:
            return
        geo = req_data.get('geo')
        user_id = geo.get('user_id') if geo else None
        if user_id is None:
            return
        pending = self._proactive_migrations.pop(user_id, None)
        if pending is None:
            return
        if geo is None:
            geo = {}
            req_data['geo'] = geo
        if sched.instance_id == pending['target_instance_id']:
            req_data['_proactive_kv_prewarm_seeded_tokens'] = pending['seeded_tokens']
            geo['proactive_kv_prewarm_seeded_tokens'] = pending['seeded_tokens']
        else:
            geo['proactive_kv_prewarm_wasted'] = 1

    def _apply_kv_migration_if_needed(self, req_data, sched):
        failover = req_data.get('failover')
        if not failover:
            return
        if failover.get('failover_mode') == 'cold':
            geo = dict(req_data.get('geo') or {})
            geo.setdefault('communication_latency_ns', 0)
            geo.setdefault('request_send_time_ns', int(req_data['arrival_time_ns']))
            req_data['geo'] = geo
            return
        if failover.get('failover_mode') == 'local_kv':
            if failover.get('_kv_migration_applied', False):
                return
            if not sched.enable_prefix_caching:
                raise RuntimeError("failover_mode=local_kv requires --enable-prefix-caching")

            input_hash_ids = req_data.get('input_hash_ids', [])
            if not input_hash_ids:
                input_hash_ids = list(range(int(req_data['input_toks'])))
                req_data['input_hash_ids'] = input_hash_ids

            requested_prefix = int(failover.get('reuse_prefix_toks', 0))
            reused_tokens = sched.memory.seed_migrated_prefix(input_hash_ids, requested_prefix)
            reuse_bytes = sched.memory.get_kv(reused_tokens) * sched.num_npus
            failover.update({
                '_kv_migration_applied': True,
                'kv_migration_tokens': reused_tokens,
                'kv_migration_bytes': reuse_bytes,
                'kv_migration_latency_ns': 0,
                'kv_migration_distance_latency_ns': 0,
                'kv_migration_serialization_latency_ns': 0,
            })
            geo = dict(req_data.get('geo') or {})
            geo.setdefault('communication_latency_ns', 0)
            geo.setdefault('request_send_time_ns', int(req_data['arrival_time_ns']))
            req_data['geo'] = geo
            return
        if failover.get('failover_mode') != 'migrate_kv':
            return
        if failover.get('_kv_migration_applied', False):
            return
        if not sched.enable_prefix_caching:
            raise RuntimeError("failover_mode=migrate_kv requires --enable-prefix-caching")

        input_hash_ids = req_data.get('input_hash_ids', [])
        if not input_hash_ids:
            input_hash_ids = list(range(int(req_data['input_toks'])))
            req_data['input_hash_ids'] = input_hash_ids

        requested_prefix = int(failover.get('reuse_prefix_toks', 0))
        migrated_tokens = sched.memory.seed_migrated_prefix(input_hash_ids, requested_prefix)

        # SPEC: 2026-07-28_proactive_kv_prewarm -- Method C credit. If a
        # background pre-warm (maybe_proactive_kv_prewarm) already seeded
        # this user's content at THIS target before the request arrived,
        # verify how much of it is still actually resident (match_prefix
        # against the real current cache state, not the possibly-stale
        # remembered seed count) and discount the billable token count
        # accordingly. Defaults to 0 (no-op) unless
        # --enable-proactive-kv-prewarm actually pre-warmed and landed on
        # this same target -- see _resolve_proactive_kv_prewarm.
        proactive_credit_tokens = 0
        if self.enable_proactive_kv_prewarm:
            seeded = int(req_data.get('_proactive_kv_prewarm_seeded_tokens', 0))
            if seeded > 0:
                match = sched.memory.npu_prefix_cache.match_prefix(
                    input_hash_ids[:migrated_tokens]
                )
                proactive_credit_tokens = min(match.hit_length, seeded, migrated_tokens)
        billable_tokens = max(0, migrated_tokens - proactive_credit_tokens)

        migration_bytes = sched.memory.get_kv(billable_tokens) * sched.num_npus
        bandwidth_mbps = float(failover['kv_migration_bandwidth_gbps']) * 1000.0
        serialization_ns = round(8000.0 * migration_bytes / bandwidth_mbps) if bandwidth_mbps > 0 else 0
        # SPEC: fixed APN propagation (10cell_apn spec section 6) overrides
        # the distance-proportional APN leg when set.
        if self.apn_fixed_propagation_ns is not None:
            distance_ns = round(self.apn_fixed_propagation_ns)
        else:
            distance_ns = round(
                float(failover['kv_migration_distance_m']) *
                float(failover['distance_latency_ns_per_meter'])
            )
        apn_leg_ns = distance_ns + serialization_ns
        # SPEC: CPU-staging KV migration time model (10cell_apn spec section
        # 8.3) -- source GPU->CPU, APN transfer, CPU->target GPU, all
        # sequential. GB/s numerically equals bytes/ns, so no unit
        # conversion is needed. Only activates when both staging params are
        # set; otherwise migration stays a single-hop distance+serialization
        # cost (existing behavior).
        if self.kv_staging_bandwidth_gbytes_per_s is not None and self.kv_staging_latency_ns is not None:
            staging_ns = self.kv_staging_latency_ns + migration_bytes / self.kv_staging_bandwidth_gbytes_per_s
            migration_ns = staging_ns + apn_leg_ns + staging_ns
        else:
            migration_ns = apn_leg_ns

        original_arrival = int(req_data['arrival_time_ns'])
        # SPEC: 2026-07-27_speculative_kv_transfer -- Method B credit. If this
        # request had a pinned speculative transfer running while the
        # redirect decision was still pending (_maybe_capacity_dynamic_formula_route),
        # subtract however much of migration_ns that transfer already covered.
        # Defaults to 0 (a no-op) unless --enable-speculative-kv-migration
        # actually pinned and committed to *this* target.
        elapsed_speculative_ns = min(
            migration_ns,
            max(0, int(req_data.get('_speculative_kv_elapsed_ns', 0))),
        )
        effective_migration_ns = migration_ns - elapsed_speculative_ns
        if self.enable_scheduler_hide_kv_migration:
            # Method A: the request becomes visible to the target scheduler's
            # queue immediately (arrival_time_ns unchanged); only the actual
            # prefill-compute start is gated on KV physically finishing, via
            # kv_ready_time_ns (see Scheduler.schedule_base/schedule_with_prefix).
            kv_ready_time_ns = original_arrival + effective_migration_ns
        else:
            # Pre-existing behavior: KV transfer and scheduler-queue waiting
            # are strictly serial. Kept byte-for-byte identical when the
            # flag is off.
            req_data['arrival_time_ns'] = original_arrival + migration_ns
            kv_ready_time_ns = req_data['arrival_time_ns']
        failover.update({
            '_kv_migration_applied': True,
            'kv_migration_tokens': migrated_tokens,
            'kv_migration_bytes': migration_bytes,
            'kv_migration_latency_ns': migration_ns,
            'kv_migration_distance_latency_ns': distance_ns,
            'kv_migration_serialization_latency_ns': serialization_ns,
            'kv_migration_effective_latency_ns': effective_migration_ns,
            'kv_migration_speculative_hidden_ns': elapsed_speculative_ns,
        })

        geo = dict(req_data.get('geo') or {})
        geo['communication_latency_ns'] = geo.get('communication_latency_ns', 0) + migration_ns
        geo.setdefault('request_send_time_ns', original_arrival)
        geo['kv_ready_time_ns'] = kv_ready_time_ns
        if self.enable_proactive_kv_prewarm:
            geo['proactive_kv_prewarm_hit_tokens'] = proactive_credit_tokens
            geo['proactive_kv_prewarm_hit'] = int(proactive_credit_tokens > 0)
            if proactive_credit_tokens > 0:
                # Confirmed a hit -- overrides _resolve_proactive_kv_prewarm's
                # default (which only knows the pre-warm *landed* here, not
                # whether match_prefix still found it resident).
                geo['proactive_kv_prewarm_wasted'] = 0
        req_data['geo'] = geo

    def _load_agentic_session(self, row, enable_prefix_caching):
        """Load an agentic session: first sub-request to pending, rest deferred."""
        sub_reqs = row['sub_requests']
        if not sub_reqs:
            return 0
        session_id = row.get('session_id', f'session_{self._next_request_id}')
        base_id = self._next_request_id
        self._next_request_id += len(sub_reqs)
        arrival_ns = int(row['arrival_time_ns'])

        # Store session state for dependency chain
        self._deferred_sessions[session_id] = {
            'sub_requests': sub_reqs,
            'next_index': 1,  # index 0 is being queued now
            'id_base': base_id,
        }

        # Queue the first sub-request
        first = sub_reqs[0]
        req_data = {
            'index': base_id,
            'input_toks': int(first['input_toks']),
            'output_toks': int(first['input_toks'] + first['output_toks']),
            'arrival_time_ns': arrival_ns,
            'session_id': session_id,
            'sub_request_index': 0,
        }
        if enable_prefix_caching:
            req_data['input_hash_ids'] = first.get('input_tok_ids', [])
            req_data['output_hash_ids'] = first.get('output_tok_ids', [])
        self._pending_requests.append(req_data)
        self._request_to_session[base_id] = (session_id, 0)

        return len(sub_reqs)

    def _select_proactive_destination(self, exclude_instance_id):
        """Pick the other prefill instance with the lowest capacity_pressure,
        request-agnostic (used to pick where to speculatively pre-warm a
        pressured instance's frequent users' cache -- see
        proactive_kv_prewarm_design.md). Mirrors the min_pressure_no_model
        selector's tie-break (lowest instance_id) for self-consistency with
        the policy that actually decides real redirects."""
        best_sched = None
        best_pressure = None
        dummy_req_data = {'output_toks': 0, 'index': None}
        for sched in self.prefill_schedulers:
            if sched.instance_id == exclude_instance_id:
                continue
            pressure = self._capacity_snapshot(sched, dummy_req_data)['capacity_pressure']
            if (best_pressure is None or pressure < best_pressure
                    or (pressure == best_pressure and sched.instance_id < best_sched.instance_id)):
                best_sched = sched
                best_pressure = pressure
        return best_sched

    def maybe_proactive_kv_prewarm(self, current_time_ns):
        """Method C trigger: scan each prefill instance's capacity pressure;
        when an instance crosses the configured threshold, speculatively
        pre-migrate the KV cache of its most frequent recent users to a
        less-loaded candidate, ahead of those users' next request arriving.
        Request-agnostic -- called unconditionally from the main loop
        alongside route_arrived_requests, not from a request's own routing
        path. See proactive_kv_prewarm_design.md for the full design and
        the accepted limitations (free transfer, no radix_tree.py changes,
        content may be stale relative to the real cache)."""
        if not self.enable_proactive_kv_prewarm:
            return
        if (self._proactive_last_scan_ns is not None
                and current_time_ns - self._proactive_last_scan_ns
                < self.proactive_kv_prewarm_eval_interval_ns):
            return
        self._proactive_last_scan_ns = current_time_ns

        dummy_req_data = {'output_toks': 0, 'index': None}
        for sched in self.prefill_schedulers:
            instance_id = sched.instance_id
            last_trigger = self._proactive_last_trigger_ns.get(instance_id)
            if (last_trigger is not None
                    and current_time_ns - last_trigger < self.proactive_kv_prewarm_cooldown_ns):
                continue

            snapshot = self._capacity_snapshot(sched, dummy_req_data)
            if snapshot['capacity_pressure'] < self.proactive_kv_prewarm_pressure_threshold:
                continue
            self._proactive_last_trigger_ns[instance_id] = current_time_ns

            history = self._instance_user_history.get(instance_id, {})
            lookback_cutoff = current_time_ns - self.proactive_kv_prewarm_lookback_ns
            counts = []
            for user_id, timestamps in history.items():
                while timestamps and timestamps[0] < lookback_cutoff:
                    timestamps.popleft()
                if timestamps:
                    counts.append((len(timestamps), timestamps[-1], user_id))
            if not counts:
                continue
            counts.sort(key=lambda item: (-item[0], -item[1], item[2]))
            top_users = [user_id for _, _, user_id in counts[:self.proactive_kv_prewarm_top_k]]

            for user_id in top_users:
                if user_id in self._proactive_migrations:
                    continue
                content = self._user_last_seen_content.get(user_id)
                if content is None:
                    continue
                if content['home_instance_id'] != instance_id:
                    continue
                if content['seen_at_ns'] < lookback_cutoff:
                    continue

                target_sched = self._select_proactive_destination(instance_id)
                if target_sched is None:
                    continue

                try:
                    seeded_tokens = target_sched.memory.seed_migrated_prefix(
                        content['input_hash_ids'], content['reuse_prefix_toks'],
                        mark_speculative=True,
                    )
                except RuntimeError:
                    # Destination couldn't free enough NPU memory even after
                    # eviction (e.g. a tightly-packed topology). Speculation
                    # is meant to be a free, best-effort background attempt
                    # -- never let it crash the simulation. Just skip this
                    # user this trigger; a later scan may find more room.
                    continue
                if seeded_tokens <= 0:
                    continue

                self._proactive_migrations[user_id] = {
                    'target_instance_id': target_sched.instance_id,
                    'source_instance_id': instance_id,
                    'seeded_tokens': seeded_tokens,
                    'seeded_at_ns': int(current_time_ns),
                }
                estimated_transfer_ns = self._kv_migration_cost_ns(
                    target_sched,
                    {'reuse_prefix_toks': seeded_tokens, 'input_toks': seeded_tokens},
                )
                self.proactive_migration_log.append({
                    'trigger_time_ns': int(current_time_ns),
                    'source_instance_id': instance_id,
                    'source_capacity_pressure': snapshot['capacity_pressure'],
                    'source_slot_pressure': snapshot['slot_pressure'],
                    'target_instance_id': target_sched.instance_id,
                    'user_id': user_id,
                    'user_recent_request_count': dict(
                        (uid, cnt) for cnt, _, uid in counts
                    ).get(user_id),
                    'seeded_tokens': seeded_tokens,
                    'estimated_transfer_ns': estimated_transfer_ns,
                })

    def save_proactive_migration_log(self, output_file):
        """Write one row per Method C trigger event (not per request) --
        diagnostics only, separate from the per-request columns appended to
        the main requests.csv in scheduler.py."""
        if not self.proactive_migration_log:
            return
        fieldnames = list(self.proactive_migration_log[0].keys())
        with open(output_file, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.proactive_migration_log)

    def route_arrived_requests(self, current_time_ns):
        """Route requests that have arrived by current_time_ns to instances.

        Called at the start of each iteration in the main simulation loop.
        Returns the number of newly routed requests.
        """
        routed = 0
        while self._pending_idx < len(self._pending_requests):
            req_data = self._pending_requests[self._pending_idx]
            if req_data['arrival_time_ns'] > current_time_ns:
                break

            self._record_initial_capacity_context(req_data)
            self._record_user_activity(req_data, current_time_ns)

            # >>> SPEC: redirect-on-capacity routing. Base router.py went
            # straight from the arrival check above to
            # `instance_id = self._select_instance(...)` below — everything
            # in this block up to (not including) the `failover = ...` line
            # is new.
            if self.routing_policy == "NEAREST_KV":
                nearest_sched = self._find_scheduler(
                    self.prefill_schedulers, req_data['assigned_instance_id']
                )
                if nearest_sched is None:
                    raise RuntimeError(
                        f"NEAREST_KV routing: assigned_instance_id "
                        f"{req_data['assigned_instance_id']} does not match any "
                        "available prefill instance."
                    )
                if not self._has_capacity(nearest_sched, req_data):
                    self._defer_capacity_retry(
                        req_data, current_time_ns, nearest_sched, nearest_sched
                    )
                    self._pending_requests.pop(self._pending_idx)
                    self._insert_pending_sorted(req_data)
                    continue
                self._clear_capacity_failure(req_data)

            if self.routing_policy == "NEAREST_REJECT" and not req_data.get('_reject_resolved', False):
                if self._maybe_reject_and_redirect(req_data, current_time_ns):
                    # Arrival time pushed back to account for the capacity-check
                    # round trip + redirect; reinsert in sorted order and retry
                    # later instead of routing it now.
                    self._pending_requests.pop(self._pending_idx)
                    self._insert_pending_sorted(req_data)
                    continue

            if self.routing_policy in ("NEAREST_MIGRATE", "NEAREST_MIGRATE_KV") and not req_data.get('_reject_resolved', False):
                if self._maybe_migrate_and_redirect(req_data, current_time_ns):
                    # Arrival time pushed back by the GPU-to-GPU backbone
                    # transfer only (the UE->GPU_A uplink already elapsed);
                    # reinsert in sorted order and retry later.
                    self._pending_requests.pop(self._pending_idx)
                    self._insert_pending_sorted(req_data)
                    continue

            if self.routing_policy == "NEAREST_SECOND_TTFT_RESERVE" and not req_data.get('_adaptive_resolved', False):
                if self._maybe_adaptive_route(req_data, current_time_ns):
                    self._pending_requests.pop(self._pending_idx)
                    self._insert_pending_sorted(req_data)
                    continue

            if self.routing_policy in (
                "NEAREST_CAPACITY_ONESHOT_KV_RESERVE",
                "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE",
            ):
                if self._maybe_capacity_oneshot_route(req_data, current_time_ns):
                    self._pending_requests.pop(self._pending_idx)
                    self._insert_pending_sorted(req_data)
                    continue

            if self.routing_policy in (
                "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE",
                *MULTI_CANDIDATE_SELECTORS,
            ):
                if self._maybe_capacity_dynamic_formula_route(
                    req_data, current_time_ns
                ):
                    self._pending_requests.pop(self._pending_idx)
                    self._insert_pending_sorted(req_data)
                    continue

            # Capacity may change while a redirected request is in transit.
            # Revalidate the chosen target immediately before final admission.
            if (
                (
                    self.routing_policy in ("NEAREST_REJECT", "NEAREST_MIGRATE", "NEAREST_MIGRATE_KV")
                    and req_data.get('_reject_resolved', False)
                ) or (
                    self.routing_policy == "NEAREST_SECOND_TTFT_RESERVE"
                    and req_data.get('_adaptive_resolved', False)
                ) or (
                    self.routing_policy in (
                        "NEAREST_CAPACITY_ONESHOT_KV_RESERVE",
                        "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE",
                        "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE",
                        *MULTI_CANDIDATE_SELECTORS,
                    )
                    and req_data.get('_oneshot_selected_route') in ('cold_migrate', 'kv_handoff')
                )
            ):
                target_sched = self._find_scheduler(
                    self.prefill_schedulers, req_data['assigned_instance_id']
                )
                if target_sched is None:
                    raise RuntimeError(
                        f"Redirect target {req_data['assigned_instance_id']} does not "
                        "match any available prefill instance."
                    )
                if not self._has_capacity(target_sched, req_data, record_failure=False):
                    if (
                        self.routing_policy ==
                        "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE"
                        and not self.enable_oneshot_target_reservation
                    ):
                        req_data.setdefault(
                            '_router_capacity_wait_start_ns', int(current_time_ns)
                        )
                        req_data['_capacity_retry_count'] = int(
                            req_data.get('_capacity_retry_count', 0)
                        ) + 1
                    req_data['arrival_time_ns'] = max(
                        int(req_data['arrival_time_ns']), int(current_time_ns) + 1
                    )
                    self._pending_requests.pop(self._pending_idx)
                    self._insert_pending_sorted(req_data)
                    continue
            # <<< SPEC: redirect-on-capacity routing

            # NOTE: the `failover` branch below is a separate, pre-existing
            # feature (static per-row KV-cache failover/migration), not part
            # of the redirect-on-capacity spec above.
            failover = req_data.get('failover')
            if failover and 'target_instance_id' in failover:
                instance_id = self._find_scheduler_index(
                    self.prefill_schedulers,
                    failover['target_instance_id'],
                )
                if instance_id is None:
                    raise RuntimeError(
                        f"failover target_instance_id {failover['target_instance_id']} "
                        f"does not match any available prefill instance"
                    )
            else:
                instance_id = self._select_instance(self.prefill_schedulers, "prefill", req_data)
            sched = self.prefill_schedulers[instance_id]
            # >>> SPEC: fair-comparison KV baseline. NEAREST_KV/NEAREST_MIGRATE_KV
            # always get the free local-cache assumption (they never leave a
            # cache behind: NEAREST_KV never redirects, NEAREST_MIGRATE_KV pays
            # to bring the cache along when it does). NEAREST_REJECT/NEAREST_MIGRATE
            # get it too, but only when this request is still on its *home* GPU
            # (`_reject_resolved` is only set once a redirect has actually fired) —
            # once redirected, those two policies have no mechanism to move the
            # cache, so the destination GPU must recompute from scratch, same as
            # in reality.
            if self.routing_policy in ("NEAREST_KV", "NEAREST_MIGRATE_KV") or (
                self.routing_policy in ("NEAREST_REJECT", "NEAREST_MIGRATE")
                and not req_data.get('_reject_resolved', False)
            ) or (
                self.routing_policy == "NEAREST_SECOND_TTFT_RESERVE"
                and not req_data.get('_adaptive_resolved', False)
            ) or (
                self.routing_policy in (
                        "NEAREST_CAPACITY_ONESHOT_KV_RESERVE",
                        "NEAREST_CAPACITY_ONESHOT_FORMULA_KV_RESERVE",
                        "NEAREST_CAPACITY_DYNAMIC_FORMULA_KV_RESERVE",
                        *MULTI_CANDIDATE_SELECTORS,
                )
                and req_data.get('_oneshot_selected_route') == 'local'
            ):
                self._attach_local_kv_reuse(req_data, sched.instance_id)
            # <<< SPEC: fair-comparison KV baseline
            self._resolve_proactive_kv_prewarm(req_data, sched)
            self._apply_kv_migration_if_needed(req_data, sched)
            self._release_adaptive_reservation(req_data)
            geo = req_data.get('geo')
            if geo is None:
                geo = {}
                req_data['geo'] = geo
            self._record_decision_capacity_context(req_data, sched)
            wait_start = req_data.get('_router_capacity_wait_start_ns')
            geo['router_capacity_wait_start_ns'] = (
                int(wait_start) if wait_start is not None else -1
            )
            geo['router_decision_time_ns'] = int(current_time_ns)
            geo['router_capacity_wait_ns'] = (
                max(0, int(current_time_ns) - int(wait_start))
                if wait_start is not None else 0
            )
            geo['router_candidate_gpu_count'] = len(self.prefill_schedulers)

            if sched.enable_prefix_caching:
                sched.add_request([
                    req_data['index'], sched.model,
                    req_data['input_toks'], req_data['output_toks'],
                    req_data['arrival_time_ns'], sched.instance_id,
                    req_data.get('input_hash_ids', []), req_data.get('output_hash_ids', []),
                ], is_init=self._is_init, geo=geo, failover=req_data.get('failover'))
            else:
                sched.add_request([
                    req_data['index'], sched.model,
                    req_data['input_toks'], req_data['output_toks'],
                    req_data['arrival_time_ns'], sched.instance_id,
                ], is_init=self._is_init, geo=geo, failover=req_data.get('failover'))

            self._pending_idx += 1
            routed += 1

        return routed

    def has_pending_requests(self):
        """Check if there are unrouted requests remaining."""
        return self._pending_idx < len(self._pending_requests)

    def get_first_arrival_time(self):
        """Return the first request's arrival time in ns, or 1 if no requests."""
        if self._pending_requests:
            return max(1, self._pending_requests[0]['arrival_time_ns'])
        return 1

    # -----------------------------------------------------------------------
    # Agentic dependency chain management
    # -----------------------------------------------------------------------

    def notify_request_completed(self, request_id, completion_time_ns):
        """Called when a request finishes. Releases the next sub-request in
        the session chain after the tool_call duration elapses.

        For flat requests (not in a session), this is a no-op.
        """
        session_info = self._request_to_session.pop(request_id, None)
        if session_info is None:
            return
        session_id, completed_idx = session_info
        session = self._deferred_sessions.get(session_id)
        if session is None:
            return

        sub_reqs = session['sub_requests']
        next_idx = session['next_index']
        base_id = session['id_base']

        # Get tool duration from the completed sub-request
        tool_duration_ns = int(sub_reqs[completed_idx].get('tool_duration_ns', 0))
        release_time_ns = completion_time_ns + tool_duration_ns

        if next_idx < len(sub_reqs):
            # Release next sub-request
            next_sub = sub_reqs[next_idx]
            next_id = base_id + next_idx
            req_data = {
                'index': next_id,
                'input_toks': int(next_sub['input_toks']),
                'output_toks': int(next_sub['input_toks'] + next_sub['output_toks']),
                'arrival_time_ns': release_time_ns,
                'session_id': session_id,
                'sub_request_index': next_idx,
            }
            if self._enable_prefix_caching:
                req_data['input_hash_ids'] = next_sub.get('input_tok_ids', [])
                req_data['output_hash_ids'] = next_sub.get('output_tok_ids', [])
            # Insert in sorted position after _pending_idx
            self._insert_pending_sorted(req_data)
            self._request_to_session[next_id] = (session_id, next_idx)
            session['next_index'] = next_idx + 1
        else:
            # Session complete — all sub-requests have been released
            del self._deferred_sessions[session_id]

    def _insert_pending_sorted(self, req_data):
        """Insert a request into _pending_requests maintaining arrival-time
        sort order for the not-yet-consumed portion (from _pending_idx onward)."""
        arrival = req_data['arrival_time_ns']
        # Binary search in the unconsumed portion
        lo = self._pending_idx
        hi = len(self._pending_requests)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._pending_requests[mid]['arrival_time_ns'] <= arrival:
                lo = mid + 1
            else:
                hi = mid
        self._pending_requests.insert(lo, req_data)

    def has_deferred_sessions(self):
        """Check if there are agentic sessions with unreleased sub-requests."""
        return bool(self._deferred_sessions)

    def get_next_pending_arrival(self):
        """Return the next pending request's arrival time, or None."""
        if self._pending_idx < len(self._pending_requests):
            return self._pending_requests[self._pending_idx]['arrival_time_ns']
        return None

    # -----------------------------------------------------------------------
    # Legacy: upfront routing (kept for backward compat)
    # -----------------------------------------------------------------------

    def generate(self, path, enable_prefix_caching=False, is_init=True):
        """Load and immediately route all requests (legacy behavior)."""
        self.load_requests(path, enable_prefix_caching, is_init)
        # Route all at once (arrival time ignored)
        self.route_arrived_requests(float('inf'))
        for scheduler in self.schedulers:
            self.logger.info(
                "Added %d requests to scheduler[%d] (%s type)",
                len(scheduler.request),
                scheduler.instance_id,
                scheduler.pd_type
            )

    def transfer_prefill_request(self, requests):
        for req in requests:
            req_data = {'input_toks': req.original_input}
            instance_id = self._select_instance(self.decode_schedulers, "decode", req_data)
            self.decode_schedulers[instance_id].add_decode(req)
