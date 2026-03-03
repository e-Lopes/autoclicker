import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ModelSnapshot:
    payload: Dict[str, Any]
    score: float
    source: str


class ModelStore:
    def __init__(self, base_dir: str, latest_name: str, best_name: str):
        self.base_dir = base_dir
        self.latest_path = os.path.join(base_dir, latest_name)
        self.best_path = os.path.join(base_dir, best_name)

    @staticmethod
    def _score(payload: Optional[Dict[str, Any]]) -> float:
        if not payload:
            return -10**9
        return max(float(payload.get("candidate_score", -10**9)), float(payload.get("best_score", -10**9)))

    @staticmethod
    def _read(path: str) -> Optional[Dict[str, Any]]:
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def _write_atomic(path: str, payload: Dict[str, Any]):
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)

    def load_best_available(self) -> Optional[ModelSnapshot]:
        p_best = self._read(self.best_path)
        p_latest = self._read(self.latest_path)
        s_best = self._score(p_best)
        s_latest = self._score(p_latest)

        if p_best is None and p_latest is None:
            return None

        if s_best >= s_latest:
            return ModelSnapshot(payload=p_best if p_best is not None else p_latest, score=max(s_best, s_latest), source="best")
        return ModelSnapshot(payload=p_latest if p_latest is not None else p_best, score=max(s_best, s_latest), source="latest")

    def save_latest(self, payload: Dict[str, Any]):
        self._write_atomic(self.latest_path, payload)

    def save_best(self, payload: Dict[str, Any]):
        self._write_atomic(self.best_path, payload)


class EventLogger:
    def __init__(self, base_dir: str, filename: str = "zenkoi_events.jsonl"):
        self.path = os.path.join(base_dir, filename)

    def log(self, event_type: str, data: Dict[str, Any]):
        row = {
            "ts": time.time(),
            "type": event_type,
            "data": data,
        }
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass


class DatasetCollector:
    def __init__(self, base_dir: str, dataset_dir: str = "dataset"):
        self.root = os.path.join(base_dir, dataset_dir)
        self.ok_dir = os.path.join(self.root, "success")
        self.fail_dir = os.path.join(self.root, "fail")
        os.makedirs(self.ok_dir, exist_ok=True)
        os.makedirs(self.fail_dir, exist_ok=True)

    def save_patch(self, bgr_img, point, success: bool, radius: int = 32):
        try:
            import cv2
            h, w = bgr_img.shape[:2]
            x, y = int(point[0]), int(point[1])
            x0, x1 = max(0, x - radius), min(w, x + radius + 1)
            y0, y1 = max(0, y - radius), min(h, y + radius + 1)
            patch = bgr_img[y0:y1, x0:x1]
            if patch.size == 0:
                return
            name = f"{int(time.time()*1000)}_{x}_{y}.png"
            out = os.path.join(self.ok_dir if success else self.fail_dir, name)
            cv2.imwrite(out, patch)
        except Exception:
            pass
