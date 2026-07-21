import csv
import json
import math
from pathlib import Path


NUMERIC_FEATURES = (
    'input_tokens', 'output_tokens', 'home_cached_prefix_tokens',
    'request_rate_rps', 'arrival_offset_s', 'interarrival_ms',
    'global_arrivals_1s', 'global_arrivals_5s',
    'home_arrivals_1s', 'home_arrivals_5s', 'home_workload_share',
    'router_initial_waiting_reqs', 'router_initial_running_reqs',
    'router_initial_required_kv_bytes', 'router_initial_available_kv_bytes',
    'router_initial_projected_active_kv_bytes',
    'router_initial_capacity_pressure', 'router_initial_slot_pressure',
    'router_initial_admissible_candidate_count',
    'router_initial_total_waiting_reqs', 'router_initial_max_waiting_reqs',
    'router_initial_total_running_reqs', 'router_initial_max_running_reqs',
    'router_initial_min_available_kv_bytes',
    'router_initial_max_available_kv_bytes',
    'router_initial_min_capacity_pressure',
    'router_initial_max_capacity_pressure',
)

POLICIES = ('NEAREST_KV', 'NEAREST_MIGRATE', 'NEAREST_MIGRATE_KV')


def _read_coefficients(path):
    with path.open(newline='') as file:
        return {
            row['term']: float(row['coefficient'])
            for row in csv.DictReader(file)
        }


class OfflineTtftFormula:
    """Dependency-free evaluator for the exported TTFT component formula."""

    def __init__(self, artifact_dir=None):
        if artifact_dir is None:
            repo_root = Path(__file__).resolve().parents[2]
            artifact_dir = (
                repo_root / 'experiments/2026-07-16_ttft_component_regression/'
                'analysis/ttft_formula'
            )
        self.artifact_dir = Path(artifact_dir)
        with (self.artifact_dir / 'numeric_feature_scaling.csv').open(newline='') as file:
            self.scaling = {
                row['feature']: (float(row['mean']), float(row['scale']))
                for row in csv.DictReader(file)
            }
        self.route_coefficients = _read_coefficients(
            self.artifact_dir / 'route_event_logistic_coefficients.csv'
        )
        self.scheduler_coefficients = _read_coefficients(
            self.artifact_dir / 'scheduler_ridge_coefficients.csv'
        )
        self.compute_coefficients = _read_coefficients(
            self.artifact_dir / 'compute_linear_coefficients.csv'
        )
        self.tail = json.loads(
            (self.artifact_dir / 'route_positive_tree_coefficients.json').read_text()
        )

    def _transformed(self, features, policy):
        transformed = {}
        for name in NUMERIC_FEATURES:
            mean, scale = self.scaling[name]
            transformed[name] = (float(features[name]) - mean) / scale
        for candidate in POLICIES:
            transformed[f'policy_{candidate}'] = float(policy == candidate)
        return transformed

    @staticmethod
    def _linear(coefficients, transformed, intercept_name):
        value = coefficients[intercept_name]
        for name, feature_value in transformed.items():
            value += coefficients.get(name, 0.0) * feature_value
        return value

    def _positive_route_ms(self, transformed):
        log_prediction = float(self.tail['initial_prediction'])
        for tree in self.tail['trees']:
            nodes = {node['node_id']: node for node in tree['nodes']}
            node = nodes[0]
            while not node['is_leaf']:
                value = transformed[node['feature']]
                child = (
                    node['left_child']
                    if value <= node['threshold_standardized']
                    else node['right_child']
                )
                node = nodes[child]
            log_prediction += float(node['weighted_leaf_coefficient'])
        lower, upper = self.tail['log_prediction_bounds']
        return math.expm1(min(max(log_prediction, lower), upper))

    def predict(self, features, policy):
        if policy not in POLICIES:
            raise ValueError(f'Unsupported TTFT formula policy: {policy}')
        transformed = self._transformed(features, policy)
        logit = self._linear(
            self.route_coefficients, transformed, 'intercept'
        )
        if logit >= 0:
            route_probability = 1.0 / (1.0 + math.exp(-logit))
        else:
            exp_logit = math.exp(logit)
            route_probability = exp_logit / (1.0 + exp_logit)
        route_positive_ms = self._positive_route_ms(transformed)
        upper = self.tail.get('upper_prediction', {})
        route_upper_ms = route_positive_ms + max(
            0.0, float(upper.get('residual_ms', 0.0))
        )
        route_ms = route_probability * route_positive_ms
        scheduler_ms = max(0.0, self._linear(
            self.scheduler_coefficients, transformed, 'intercept_ms'
        ))
        compute_ms = max(
            0.0,
            self.compute_coefficients['intercept_ms']
            + self.compute_coefficients['input_tokens'] * features['input_tokens']
            + self.compute_coefficients['home_cached_prefix_tokens']
            * features['home_cached_prefix_tokens'],
        )
        return {
            'route_probability': route_probability,
            'route_positive_ms': route_positive_ms,
            'route_upper_ms': route_upper_ms,
            'route_ms': route_ms,
            'scheduler_ms': scheduler_ms,
            'compute_ms': compute_ms,
            'ttft_ms': route_ms + scheduler_ms + compute_ms,
        }
