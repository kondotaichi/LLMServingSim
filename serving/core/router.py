import bisect
import json
import random
from .logger import get_logger


# Geographic/communication fields written by
# `python -m workloads.generators geographic` (Phase 1 spec section 20/22.3).
# Present only on geographic workloads; absent on ordinary flat JSONL rows.
_GEO_FIELDS = (
    'user_id', 'user_x_m', 'user_y_m',
    'gpu_id', 'gpu_x_m', 'gpu_y_m', 'distance_m', 'assigned_instance_id',
    'network_throughput_mbps',
    'request_payload_bytes', 'first_token_payload_bytes',
    'uplink_distance_latency_ns', 'uplink_serialization_latency_ns', 'uplink_latency_ns',
    'downlink_distance_latency_ns', 'downlink_serialization_latency_ns', 'downlink_latency_ns',
    'communication_latency_ns',
    'request_send_time_ns',
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
            seed=42
    ):
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
        else:
            raise ValueError(f"Unknown routing_policy '{routing_policy}'. "
                             "Supported: RR, RAND, LOAD, PROMPT, QUEUE, HYBRID, CUSTOM, NEAREST")
        self.logger = get_logger(self.__class__)

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

    # -----------------------------------------------------------------------
    # Request loading and real-time routing
    # -----------------------------------------------------------------------

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

        self.logger.info("Loaded %d requests into pending queue "
                         "(%d agentic sessions deferred)",
                         len(self._pending_requests),
                         len(self._deferred_sessions))

    def _load_flat_request(self, row, enable_prefix_caching):
        """Load a single flat request into pending queue."""
        req_id = self._next_request_id
        self._next_request_id += 1
        req_data = {
            'index': req_id,
            'input_toks': int(row['input_toks']),
            'output_toks': int(row['input_toks'] + row['output_toks']),
            'arrival_time_ns': int(row['arrival_time_ns']),
        }
        if enable_prefix_caching:
            req_data['input_hash_ids'] = row.get('input_tok_ids', [])
            req_data['output_hash_ids'] = row.get('output_tok_ids', [])
        if 'assigned_instance_id' in row:
            req_data['assigned_instance_id'] = int(row['assigned_instance_id'])
        geo = _extract_geo_fields(row)
        if geo is not None:
            req_data['geo'] = geo
        self._pending_requests.append(req_data)

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

            instance_id = self._select_instance(self.prefill_schedulers, "prefill", req_data)
            sched = self.prefill_schedulers[instance_id]
            geo = req_data.get('geo')

            if sched.enable_prefix_caching:
                sched.add_request([
                    req_data['index'], sched.model,
                    req_data['input_toks'], req_data['output_toks'],
                    req_data['arrival_time_ns'], sched.instance_id,
                    req_data.get('input_hash_ids', []), req_data.get('output_hash_ids', []),
                ], is_init=self._is_init, geo=geo)
            else:
                sched.add_request([
                    req_data['index'], sched.model,
                    req_data['input_toks'], req_data['output_toks'],
                    req_data['arrival_time_ns'], sched.instance_id,
                ], is_init=self._is_init, geo=geo)

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
