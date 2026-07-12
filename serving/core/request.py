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

        # --- Queueing / prefill / decode timing instrumentation ---
        self.first_schedule_time_ns = -1
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
    
