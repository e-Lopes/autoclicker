from collections import defaultdict
from dataclasses import dataclass
import random


@dataclass
class CandidateFeatures:
    dist_bin: int
    area_bin: int
    risk_bin: int
    goal_bin: int
    angle_bin: int
    stall_bin: int


class AdaptiveQPolicy:
    def __init__(self):
        self.q = defaultdict(float)
        self.n = defaultdict(int)
        self.epsilon = 0.2
        self.min_epsilon = 0.05
        self.decay = 0.9995

    @staticmethod
    def state_tuple(features: CandidateFeatures):
        return (
            features.dist_bin,
            features.area_bin,
            features.risk_bin,
            features.goal_bin,
            features.angle_bin,
            features.stall_bin,
        )

    def choose(self, feature_map, force_explore=False, epsilon_override=None):
        if not feature_map:
            return None, None, False

        keys = list(feature_map.keys())
        epsilon = self.epsilon if epsilon_override is None else float(epsilon_override)
        if force_explore or random.random() < epsilon:
            k = random.choice(keys)
            return k, self.state_tuple(feature_map[k]), True

        best_key = None
        best_state = None
        best_q = -10**9
        for k in keys:
            s = self.state_tuple(feature_map[k])
            v = self.q[s]
            if v > best_q:
                best_q = v
                best_key = k
                best_state = s
        return best_key, best_state, False

    def update(self, state, reward):
        self.n[state] += 1
        alpha = 1.0 / self.n[state]
        self.q[state] = self.q[state] + alpha * (reward - self.q[state])
        self.epsilon = max(self.min_epsilon, self.epsilon * self.decay)

    def to_dict(self):
        q_out = {}
        n_out = {}
        keys = set(list(self.q.keys()) + list(self.n.keys()))
        for state in keys:
            k = "|".join(str(int(x)) for x in state)
            q_out[k] = float(self.q[state])
            n_out[k] = int(self.n[state])
        return {
            "q": q_out,
            "n": n_out,
            "epsilon": float(self.epsilon),
            "min_epsilon": float(self.min_epsilon),
            "decay": float(self.decay),
        }

    def load_dict(self, payload):
        self.q = defaultdict(float)
        self.n = defaultdict(int)
        for k, v in payload.get("q", {}).items():
            state = tuple(int(x) for x in k.split("|"))
            self.q[state] = float(v)
        for k, v in payload.get("n", {}).items():
            state = tuple(int(x) for x in k.split("|"))
            self.n[state] = int(v)
        self.epsilon = float(payload.get("epsilon", self.epsilon))
        self.min_epsilon = float(payload.get("min_epsilon", self.min_epsilon))
        self.decay = float(payload.get("decay", self.decay))
