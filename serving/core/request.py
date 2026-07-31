def _argmax_label(pairs):
    """Return the label of the largest value; ties resolved by list order (first wins)."""
    best_label, best_val = pairs[0]
    for label, val in pairs[1:]:
        if val > best_val:
            best_label, best_val = label, val
    return best_label


# class that manages request of astra-sim
class Request:
    def __init__(self, id, model, input, output, arrival, instance_id, input_hash_ids=None, output_hash_ids=None, is_init=True, geo=None, failover=None):
        self.id = id
        self.model = model
        self.input = input  # Always keep original input length
        self.output = output
        self.arrival = arrival
        self.instance_id = instance_id
        self.is_init = is_init
        self.original_input = input
        self.num_computed_tokens = 0  # Tracks actual computed tokens (vLLM style)
        self.evict = False
        self.end_time = -1
        self.latency = -1
        self.queuing_delay = -1
        self.ttft = -1
        self.tpot = -1
        self.itl = []
        self.recent_end = 0

        # For chunked prefill
        self.chunk_len = 0  # tokens scheduled for this request in the current step

        # For prefix caching modeling
        self.input_hash_ids = input_hash_ids
        self.output_hash_ids = output_hash_ids
        self.prefix_cache_hit = 0
        self.npu_cache_hit = 0
        self.storage_cache_hit = 0
        self.npu_last_node = None
        self.cpu_last_node = None
        self.storage_last_node = None

        # For prefix cache lock tracking
        self._prefix_locked = False
        self._prefix_npu_stats_counted = False
        self._prefix_storage_stats_counted = False

        # For agentic session tracking (informational, does not drive scheduling)
        self.session_id = None
        self.sub_request_index = None

        # --- Geographic distributed-inference simulation (Phase 1) ---
        # `geo` is populated only for workloads produced by
        # `python -m workloads.generators geographic`; absent (None) for
        # ordinary flat/agentic workloads, in which case every field below
        # keeps a benign default and all derived geo metrics stay at -1.
        geo = geo or {}
        self.user_id = geo.get('user_id')
        self.user_x_m = geo.get('user_x_m')
        self.user_y_m = geo.get('user_y_m')
        self.gpu_id = geo.get('gpu_id')
        self.gpu_x_m = geo.get('gpu_x_m')
        self.gpu_y_m = geo.get('gpu_y_m')
        self.distance_m = geo.get('distance_m')
        self.assigned_instance_id = geo.get('assigned_instance_id')
        self.network_throughput_mbps = geo.get('network_throughput_mbps')
        self.request_payload_bytes = geo.get('request_payload_bytes')
        self.first_token_payload_bytes = geo.get('first_token_payload_bytes')
        self.uplink_distance_latency_ns = geo.get('uplink_distance_latency_ns', 0)
        self.uplink_serialization_latency_ns = geo.get('uplink_serialization_latency_ns', 0)
        self.uplink_latency_ns = geo.get('uplink_latency_ns', 0)
        self.downlink_distance_latency_ns = geo.get('downlink_distance_latency_ns', 0)
        self.downlink_serialization_latency_ns = geo.get('downlink_serialization_latency_ns', 0)
        self.downlink_latency_ns = geo.get('downlink_latency_ns', 0)
        self.communication_latency_ns = geo.get('communication_latency_ns', 0)
        self.request_send_time_ns = geo.get('request_send_time_ns')  # None => old-format workload

        # >>> SPEC: redirect-on-capacity routing (queue-vs-redirect experiment) ---
        # Not present pre-spec. Populated only by the NEAREST_REJECT (UE
        # resend), NEAREST_MIGRATE, and NEAREST_MIGRATE_KV routing policies
        # in router.py; every other policy leaves these at their defaults.
        self.nearest_gpu_id = geo.get('nearest_gpu_id', geo.get('gpu_id'))
        self.rerouted = geo.get('rerouted', 0)
        self.reject_penalty_ns = geo.get('reject_penalty_ns', 0)
        self.migration_latency_ns = geo.get('migration_latency_ns', 0)
        self.redirect_capacity_reason = geo.get('redirect_capacity_reason', '')
        self.capacity_running_reqs = geo.get('capacity_running_reqs', '')
        self.capacity_max_num_seqs = geo.get('capacity_max_num_seqs', '')
        self.capacity_required_kv_bytes = geo.get('capacity_required_kv_bytes', '')
        self.capacity_free_npu_bytes = geo.get('capacity_free_npu_bytes', '')
        self.capacity_projected_active_kv_bytes = geo.get('capacity_projected_active_kv_bytes', '')
        self.capacity_kv_budget_bytes = geo.get('capacity_kv_budget_bytes', '')
        self.capacity_available_kv_bytes = geo.get('capacity_available_kv_bytes', '')
        self.router_capacity_wait_start_ns = geo.get('router_capacity_wait_start_ns', -1)
        self.router_decision_time_ns = geo.get('router_decision_time_ns', -1)
        self.router_capacity_wait_ns = geo.get('router_capacity_wait_ns', 0)
        self.router_candidate_gpu_count = geo.get('router_candidate_gpu_count', '')
        self.router_capacity_retry_count = geo.get('router_capacity_retry_count', 0)
        self.router_initial_instance_id = geo.get('router_initial_instance_id', '')
        self.router_initial_waiting_reqs = geo.get('router_initial_waiting_reqs', '')
        self.router_initial_running_reqs = geo.get('router_initial_running_reqs', '')
        self.router_initial_max_num_seqs = geo.get('router_initial_max_num_seqs', '')
        self.router_initial_required_kv_bytes = geo.get('router_initial_required_kv_bytes', '')
        self.router_initial_free_npu_bytes = geo.get('router_initial_free_npu_bytes', '')
        self.router_initial_projected_active_kv_bytes = geo.get('router_initial_projected_active_kv_bytes', '')
        self.router_initial_kv_budget_bytes = geo.get('router_initial_kv_budget_bytes', '')
        self.router_initial_available_kv_bytes = geo.get('router_initial_available_kv_bytes', '')
        self.router_initial_capacity_pressure = geo.get('router_initial_capacity_pressure', '')
        self.router_initial_slot_pressure = geo.get('router_initial_slot_pressure', '')
        self.router_initial_admissible = geo.get('router_initial_admissible', '')
        self.router_initial_candidate_count = geo.get('router_initial_candidate_count', '')
        self.router_initial_admissible_candidate_count = geo.get('router_initial_admissible_candidate_count', '')
        self.router_initial_total_waiting_reqs = geo.get('router_initial_total_waiting_reqs', '')
        self.router_initial_max_waiting_reqs = geo.get('router_initial_max_waiting_reqs', '')
        self.router_initial_total_running_reqs = geo.get('router_initial_total_running_reqs', '')
        self.router_initial_max_running_reqs = geo.get('router_initial_max_running_reqs', '')
        self.router_initial_min_available_kv_bytes = geo.get('router_initial_min_available_kv_bytes', '')
        self.router_initial_max_available_kv_bytes = geo.get('router_initial_max_available_kv_bytes', '')
        self.router_initial_min_capacity_pressure = geo.get('router_initial_min_capacity_pressure', '')
        self.router_initial_max_capacity_pressure = geo.get('router_initial_max_capacity_pressure', '')
        self.router_initial_target_instance_id = geo.get('router_initial_target_instance_id', '')
        self.router_initial_target_waiting_reqs = geo.get('router_initial_target_waiting_reqs', '')
        self.router_initial_target_running_reqs = geo.get('router_initial_target_running_reqs', '')
        self.router_initial_target_max_num_seqs = geo.get('router_initial_target_max_num_seqs', '')
        self.router_initial_target_required_kv_bytes = geo.get('router_initial_target_required_kv_bytes', '')
        self.router_initial_target_free_npu_bytes = geo.get('router_initial_target_free_npu_bytes', '')
        self.router_initial_target_projected_active_kv_bytes = geo.get('router_initial_target_projected_active_kv_bytes', '')
        self.router_initial_target_kv_budget_bytes = geo.get('router_initial_target_kv_budget_bytes', '')
        self.router_initial_target_available_kv_bytes = geo.get('router_initial_target_available_kv_bytes', '')
        self.router_initial_target_capacity_pressure = geo.get('router_initial_target_capacity_pressure', '')
        self.router_initial_target_slot_pressure = geo.get('router_initial_target_slot_pressure', '')
        self.router_initial_target_admissible = geo.get('router_initial_target_admissible', '')
        self.router_decision_instance_id = geo.get('router_decision_instance_id', '')
        self.router_decision_waiting_reqs = geo.get('router_decision_waiting_reqs', '')
        self.router_decision_running_reqs = geo.get('router_decision_running_reqs', '')
        self.router_decision_max_num_seqs = geo.get('router_decision_max_num_seqs', '')
        self.router_decision_required_kv_bytes = geo.get('router_decision_required_kv_bytes', '')
        self.router_decision_free_npu_bytes = geo.get('router_decision_free_npu_bytes', '')
        self.router_decision_projected_active_kv_bytes = geo.get('router_decision_projected_active_kv_bytes', '')
        self.router_decision_kv_budget_bytes = geo.get('router_decision_kv_budget_bytes', '')
        self.router_decision_available_kv_bytes = geo.get('router_decision_available_kv_bytes', '')
        self.router_decision_capacity_pressure = geo.get('router_decision_capacity_pressure', '')
        self.router_decision_slot_pressure = geo.get('router_decision_slot_pressure', '')
        self.router_decision_admissible = geo.get('router_decision_admissible', '')
        self.router_first_block_reason = geo.get('router_first_block_reason', '')
        self.router_first_block_instance_id = geo.get('router_first_block_instance_id', '')
        self.adaptive_selected_route = geo.get('adaptive_selected_route', '')
        self.adaptive_predicted_local_ttft_ns = geo.get('adaptive_predicted_local_ttft_ns', '')
        self.adaptive_predicted_cold_ttft_ns = geo.get('adaptive_predicted_cold_ttft_ns', '')
        self.adaptive_predicted_kv_ttft_ns = geo.get('adaptive_predicted_kv_ttft_ns', '')
        self.adaptive_predicted_selected_ttft_ns = geo.get('adaptive_predicted_selected_ttft_ns', '')
        self.adaptive_target_reserved_kv_bytes = geo.get('adaptive_target_reserved_kv_bytes', 0)
        self.adaptive_target_reserved_prefill_tokens = geo.get('adaptive_target_reserved_prefill_tokens', 0)
        self.oneshot_selected_route = geo.get('oneshot_selected_route', '')
        self.oneshot_decision_reason = geo.get('oneshot_decision_reason', '')
        self.oneshot_decision_time_ns = geo.get('oneshot_decision_time_ns', -1)
        self.oneshot_predicted_local_wait_ns = geo.get('oneshot_predicted_local_wait_ns', '')
        self.oneshot_predicted_local_ttft_ns = geo.get('oneshot_predicted_local_ttft_ns', '')
        self.oneshot_predicted_redirect_ttft_ns = geo.get('oneshot_predicted_redirect_ttft_ns', '')
        self.oneshot_redirect_margin_ns = geo.get('oneshot_redirect_margin_ns', '')
        self.oneshot_max_local_wait_ns = geo.get('oneshot_max_local_wait_ns', '')
        self.oneshot_target_admissible = geo.get('oneshot_target_admissible', '')
        self.oneshot_target_reservation_enabled = geo.get(
            'oneshot_target_reservation_enabled', ''
        )
        self.oneshot_prediction_model = geo.get('oneshot_prediction_model', '')
        self.oneshot_formula_local_route_probability = geo.get('oneshot_formula_local_route_probability', '')
        self.oneshot_formula_local_route_positive_ms = geo.get('oneshot_formula_local_route_positive_ms', '')
        self.oneshot_formula_local_route_ms = geo.get('oneshot_formula_local_route_ms', '')
        self.oneshot_formula_local_scheduler_ms = geo.get('oneshot_formula_local_scheduler_ms', '')
        self.oneshot_formula_local_compute_ms = geo.get('oneshot_formula_local_compute_ms', '')
        self.oneshot_formula_redirect_route_probability = geo.get('oneshot_formula_redirect_route_probability', '')
        self.oneshot_formula_redirect_route_positive_ms = geo.get('oneshot_formula_redirect_route_positive_ms', '')
        self.oneshot_formula_redirect_route_ms = geo.get('oneshot_formula_redirect_route_ms', '')
        self.oneshot_formula_redirect_scheduler_ms = geo.get('oneshot_formula_redirect_scheduler_ms', '')
        self.oneshot_formula_redirect_compute_ms = geo.get('oneshot_formula_redirect_compute_ms', '')
        # <<< SPEC: redirect-on-capacity routing -----------------------------

        # --- KV-cache failover / migration simulation (pre-existing, unrelated to the spec above) ---
        failover = failover or {}
        self.failover_mode = failover.get('failover_mode', '')
        self.failed_instance_id = failover.get('failed_instance_id', '')
        self.failover_target_instance_id = failover.get('target_instance_id', '')
        self.reuse_prefix_toks = failover.get('reuse_prefix_toks', 0)
        self.kv_migration_tokens = failover.get('kv_migration_tokens', 0)
        self.kv_migration_bytes = failover.get('kv_migration_bytes', 0)
        self.kv_migration_latency_ns = failover.get('kv_migration_latency_ns', 0)
        self.kv_migration_distance_latency_ns = failover.get('kv_migration_distance_latency_ns', 0)
        self.kv_migration_serialization_latency_ns = failover.get('kv_migration_serialization_latency_ns', 0)
        self.kv_migration_bandwidth_gbps = failover.get('kv_migration_bandwidth_gbps', 0)
        self.kv_migration_distance_m = failover.get('kv_migration_distance_m', 0)

        # --- Scheduler-hide KV migration overlap (opt-in via
        # --enable-scheduler-hide-kv-migration; defaults below preserve the
        # pre-existing serial KV-transfer-then-queue behavior exactly) ---
        self.kv_ready_time_ns = geo.get('kv_ready_time_ns', arrival)
        self.kv_migration_effective_latency_ns = failover.get(
            'kv_migration_effective_latency_ns', self.kv_migration_latency_ns
        )
        self.kv_migration_speculative_hidden_ns = failover.get(
            'kv_migration_speculative_hidden_ns', 0
        )

        # --- Speculative KV pre-transfer (opt-in via
        # --enable-speculative-kv-migration) ---
        self.speculative_kv_target_instance_id = geo.get(
            'speculative_kv_target_instance_id', ''
        )
        self.speculative_kv_pin_time_ns = geo.get(
            'speculative_kv_pinned_at_ns', -1
        )
        self.speculative_kv_elapsed_ns = geo.get('speculative_kv_elapsed_ns', 0)
        self.speculative_kv_wasted = geo.get('speculative_kv_wasted', '')
        self.speculative_kv_wasted_ns = geo.get('speculative_kv_wasted_ns', 0)

        # --- Proactive KV pre-warm (opt-in via
        # --enable-proactive-kv-prewarm; a request-independent background
        # process, separate from the speculative pre-transfer above) ---
        self.proactive_kv_prewarm_hit = geo.get('proactive_kv_prewarm_hit', '')
        self.proactive_kv_prewarm_hit_tokens = geo.get(
            'proactive_kv_prewarm_hit_tokens', 0
        )
        self.proactive_kv_prewarm_seeded_tokens = geo.get(
            'proactive_kv_prewarm_seeded_tokens', 0
        )
        self.proactive_kv_prewarm_wasted = geo.get('proactive_kv_prewarm_wasted', '')
        self.proactive_kv_prewarm_pin_applied = geo.get(
            'proactive_kv_prewarm_pin_applied', ''
        )

        # --- Queueing / prefill / decode timing instrumentation ---
        self.first_schedule_time_ns = -1
        self.scheduler_waiting_reqs_at_first_schedule = -1
        self.scheduler_running_reqs_at_first_schedule = -1
        self.scheduler_running_decode_reqs_at_first_schedule = -1
        self.scheduler_token_budget_at_first_schedule = -1
        self.scheduler_scheduled_prefill_tokens = -1
        self.scheduler_scheduled_decode_tokens = -1
        self.scheduler_batch_num_seqs = -1
        self.scheduler_prefill_tokens_ahead = -1
        self.scheduler_admission_time_ns = -1
        self.scheduler_waiting_reqs_at_admission = -1
        self.scheduler_running_reqs_at_admission = -1
        self.scheduler_running_decode_reqs_at_admission = -1
        self.scheduler_token_budget_at_admission = -1
        self.scheduler_inflight_prefill_tokens_at_admission = -1
        self.scheduler_inflight_decode_tokens_at_admission = -1
        self.scheduler_available_token_budget_at_admission = -1
        self.scheduler_prefill_tokens_ahead_at_admission = -1
        self.waiting_since_ns = self.arrival  # starts "waiting" the instant it arrives at the GPU
        self.queueing_before_ttft_ns = 0
        self.decode_queueing_ns = 0
        self.prefill_service_ns = 0
        self.decode_active_ns = 0

        # --- TTFT / completion timestamps and derived metrics ---
        self.first_token_ready_time_ns = -1
        self.first_token_received_time_ns = -1
        self.request_end_time_ns = -1
        self.decode_after_ttft_ns = -1
        self.e2e_ttft_ns = -1
        self.request_completion_latency_ns = -1

        # --- Bottleneck analysis ---
        self.ttft_bottleneck = None
        self.total_latency_bottleneck = None
        self.communication_ratio = -1.0
        self.queueing_ratio = -1.0
        self.prefill_ratio = -1.0
        self.decode_ratio = -1.0

    # to print the request information
    def __str__(self):
        return str(self.__dict__) 

    def add_latency(self, end_time):
        self.end_time = end_time
        self.latency = self.end_time - self.arrival
        self.input = self.original_input
        if self.output == self.input + 1:
            self.tpot = 0
        else:
            self.tpot = (self.latency - self.ttft) // (self.output - self.input - 1)

        # --- geographic total-latency finalization (Phase 1) ---
        self.request_end_time_ns = end_time
        self.decode_after_ttft_ns = self.decode_queueing_ns + self.decode_active_ns
        if self.request_send_time_ns is not None:
            self.request_completion_latency_ns = self.request_end_time_ns - self.request_send_time_ns
        comm = self.communication_latency_ns
        q = self.queueing_before_ttft_ns
        pf = self.prefill_service_ns
        dec = self.decode_after_ttft_ns
        self.total_latency_bottleneck = _argmax_label(
            [("queueing", q), ("prefill", pf), ("decode", dec), ("communication", comm)])
        total = comm + q + pf + dec
        if total > 0:
            self.decode_ratio = dec / total

    def add_itl(self, current): #
        self.itl.append(current - self.recent_end)
        self.recent_end = current

    def set_que_delay(self, current):
        self.queuing_delay = current - self.arrival

    def account_admission(self, current):
        """Accumulate the wait interval [waiting_since_ns, current) into the
        pre-TTFT or post-TTFT bucket, then mark the request as active.

        Called for every request admitted into a batch (unconditionally, not
        gated on is_init) so chunked-prefill inter-chunk waits and any
        decode-phase re-waits are captured, not just the first admission.
        """
        if self.waiting_since_ns is not None:
            delta = current - self.waiting_since_ns
            if self.ttft == -1:
                self.queueing_before_ttft_ns += delta
            else:
                self.decode_queueing_ns += delta
            self.waiting_since_ns = None

    def set_ttft(self, current):
        self.ttft = current - self.arrival
        self.recent_end = current

        # --- geographic TTFT finalization (Phase 1) ---
        self.first_token_ready_time_ns = current
        self.first_token_received_time_ns = current + self.downlink_latency_ns
        if self.request_send_time_ns is not None:
            self.e2e_ttft_ns = self.first_token_received_time_ns - self.request_send_time_ns
        comm = self.communication_latency_ns
        q = self.queueing_before_ttft_ns
        pf = self.prefill_service_ns
        self.ttft_bottleneck = _argmax_label([("queueing", q), ("prefill", pf), ("communication", comm)])
        denom = comm + q + pf
        if denom > 0:
            self.communication_ratio = comm / denom
            self.queueing_ratio = q / denom
            self.prefill_ratio = pf / denom

    def log(self):
        print("         scheduled request : {}".format(self.__dict__))
    
    def is_prefill(self):
        """Check if request is still in prefill phase (has tokens left to compute)"""
        return self.num_computed_tokens < self.original_input

# class that manages batch of astra-sim
class Batch:
    def __init__(self, batch_id, model, total_len, kv_len, q_list, k_list, num_prefill, num_decode, prefill_q_list, prefill_k_list, decode_k_list, batch_time, kv_size, evict=0, load=0):
        self.batch_id = batch_id
        self.model = model
        self.total_len = total_len
        self.kv_len = kv_len
        self.batch_time = batch_time  # start time (current at construction)
        self.finish_time_ns = -1      # stamped in Scheduler.add_done once all NPUs report done
        self.fired = [] # systems that fired this batch
        self.requests = []
        self.end = []
        # vllm
        self.kv_size = kv_size
        self.evict = evict
        self.load = load
        # for attn prediction
        self.q_list = q_list
        self.k_list = k_list
        self.num_prefill = num_prefill
        self.num_decode = num_decode
        self.prefill_q_list = prefill_q_list
        self.prefill_k_list = prefill_k_list
        self.decode_k_list = decode_k_list

        # for debugging
        self.scheduled_tokens = None
    def log(self):
        print("-------------------------Batch Log------------------------")
        for key in self.__dict__.keys():
            if key == 'requests':
                continue
            print("         {} : {}".format(key, self.__dict__[key]))
        for req in self.requests:
            req.log()
        print("----------------------------------------------------------")
    
