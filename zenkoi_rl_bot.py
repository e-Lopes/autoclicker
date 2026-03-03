import ctypes
import json
import os
import random
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from collections import deque

import cv2
import numpy as np
import win32api
import win32con
import win32gui
import win32process
from PIL import ImageGrab
import tkinter as tk
from tkinter import messagebox, ttk
from zenkoi_detector import TemporalContext, bins_from_candidate
from zenkoi_policy import AdaptiveQPolicy, CandidateFeatures
from zenkoi_reward import RewardEngine
from zenkoi_storage import DatasetCollector, EventLogger, ModelStore


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
AUTO_TARGET_HINT = "zen koi"
MODEL_LATEST_FILE = "zenkoi_model_latest.json"
MODEL_BEST_FILE = "zenkoi_model_best.json"
DEFAULT_SCAN = 0.15
DEFAULT_CHASE = 0.25
DEFAULT_EPS = 0.10


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    process: str
    display: str


@dataclass
class Candidate:
    point: tuple
    area: float


@dataclass
class ColorSpec:
    lower: np.ndarray
    upper: np.ndarray
    sample_hsv: tuple


def get_process_name(pid: int) -> str:
    try:
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return f"PID {pid}"
        try:
            size = ctypes.c_uint(260)
            buf = ctypes.create_unicode_buffer(size.value)
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
            if not ok:
                return f"PID {pid}"
            return os.path.basename(buf.value)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return f"PID {pid}"


def is_taskbar_like_window(hwnd: int) -> bool:
    if not win32gui.IsWindowVisible(hwnd):
        return False
    title = win32gui.GetWindowText(hwnd)
    if not title.strip():
        return False
    if win32gui.GetWindow(hwnd, win32con.GW_OWNER) != 0:
        return False
    exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if exstyle & win32con.WS_EX_TOOLWINDOW:
        return False
    return True


def list_selectable_windows():
    windows = []

    def enum_cb(hwnd, _):
        if not is_taskbar_like_window(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = get_process_name(pid)
        display = f"{title} [{proc} | PID {pid}]"
        windows.append(WindowInfo(hwnd=hwnd, title=title, pid=pid, process=proc, display=display))

    win32gui.EnumWindows(enum_cb, None)
    windows.sort(key=lambda w: w.display.lower())
    return windows


def client_rect_on_screen(hwnd: int):
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    sx, sy = win32gui.ClientToScreen(hwnd, (left, top))
    ex, ey = win32gui.ClientToScreen(hwnd, (right, bottom))
    return sx, sy, ex, ey


def capture_client_bgr(hwnd: int):
    sx, sy, ex, ey = client_rect_on_screen(hwnd)
    if ex <= sx or ey <= sy:
        return None, (0, 0)
    img = ImageGrab.grab(bbox=(sx, sy, ex, ey))
    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    return bgr, (sx, sy)


def build_color_spec(sample_hsv: tuple, h_tol: int, s_tol: int, v_tol: int) -> ColorSpec:
    h, s, v = sample_hsv
    lower = np.array([
        max(0, h - h_tol),
        max(0, s - s_tol),
        max(0, v - v_tol),
    ], dtype=np.uint8)
    upper = np.array([
        min(179, h + h_tol),
        min(255, s + s_tol),
        min(255, v + v_tol),
    ], dtype=np.uint8)
    return ColorSpec(lower=lower, upper=upper, sample_hsv=sample_hsv)


def detect_candidates(hsv_img: np.ndarray, spec: ColorSpec, min_area: int, max_area: int):
    mask = cv2.inRange(hsv_img, spec.lower, spec.upper)
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area or area > max_area:
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        cx = int(m["m10"] / m["m00"])
        cy = int(m["m01"] / m["m00"])
        out.append(Candidate(point=(cx, cy), area=area))
    return out


def detect_candidates_multi(hsv_img: np.ndarray, specs, min_area: int, max_area: int):
    if not specs:
        return []

    merged = []
    for spec in specs:
        for cand in detect_candidates(hsv_img, spec, min_area=min_area, max_area=max_area):
            replaced = False
            for i, existing in enumerate(merged):
                ex, ey = existing.point
                cx, cy = cand.point
                if ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5 <= 10:
                    if cand.area > existing.area:
                        merged[i] = cand
                    replaced = True
                    break
            if not replaced:
                merged.append(cand)
    return merged


def dedupe_candidates(candidates, merge_radius=10):
    merged = []
    for cand in candidates:
        replaced = False
        for i, existing in enumerate(merged):
            ex, ey = existing.point
            cx, cy = cand.point
            if ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5 <= merge_radius:
                if cand.area > existing.area:
                    merged[i] = cand
                replaced = True
                break
        if not replaced:
            merged.append(cand)
    return merged


def detect_candidates_auto(hsv_img: np.ndarray, bgr_img: np.ndarray, min_area: int, max_area: int):
    # Detecta iscas de multiplas aparencias: coloridas, claras e marrons.
    h, s, v = cv2.split(hsv_img)

    mask_color = cv2.inRange(s, 55, 255)
    mask_color = cv2.bitwise_and(mask_color, cv2.inRange(v, 50, 255))

    # Iscas claras (branco/azulado) costumam ter brilho alto e saturacao media/baixa.
    mask_bright = cv2.inRange(v, 170, 255)
    mask_low_sat = cv2.inRange(s, 0, 95)
    mask_bright = cv2.bitwise_and(mask_bright, mask_low_sat)

    # Iscas marrons/douradas de objetivo.
    mask_brown_h1 = cv2.inRange(h, 8, 30)
    mask_brown_h2 = cv2.inRange(h, 160, 179)
    mask_brown_h = cv2.bitwise_or(mask_brown_h1, mask_brown_h2)
    mask_brown = cv2.bitwise_and(mask_brown_h, cv2.inRange(s, 45, 255))
    mask_brown = cv2.bitwise_and(mask_brown, cv2.inRange(v, 45, 255))

    mask = cv2.bitwise_or(mask_color, mask_bright)
    mask = cv2.bitwise_or(mask, mask_brown)

    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        area = cv2.contourArea(c)
        if area < min_area or area > max_area:
            continue

        x, y, w, hbox = cv2.boundingRect(c)
        if w <= 0 or hbox <= 0:
            continue

        aspect = w / float(hbox)
        if aspect < 0.2 or aspect > 5.0:
            continue

        peri = cv2.arcLength(c, True)
        circularity = 0.0 if peri == 0 else (4 * np.pi * area) / (peri * peri)

        roi_edges = edges[y : y + hbox, x : x + w]
        edge_density = float(np.count_nonzero(roi_edges)) / float(w * hbox)

        # Remove blobs de fundo muito lisos; mantem formas de isca.
        if edge_density < 0.008 and circularity < 0.06:
            continue

        m = cv2.moments(c)
        if m["m00"] == 0:
            continue

        cx = int(m["m10"] / m["m00"])
        cy = int(m["m01"] / m["m00"])
        out.append(Candidate(point=(cx, cy), area=area))

    return dedupe_candidates(out, merge_radius=12)


def nearest_distance(points, target):
    if not points:
        return 10**9
    tx, ty = target
    dists = [((x - tx) ** 2 + (y - ty) ** 2) ** 0.5 for x, y in points]
    return min(dists)


def point_exists_near(candidates, target, radius=26):
    tx, ty = target
    for c in candidates:
        x, y = c.point
        if ((x - tx) ** 2 + (y - ty) ** 2) ** 0.5 <= radius:
            return True
    return False


def send_client_click(hwnd: int, x: int, y: int):
    lparam = win32api.MAKELONG(int(x), int(y))
    win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
    win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)


def is_forbidden_ui_point(x: int, y: int, w: int, h: int):
    # Barra superior (level, moedas, botao +, etc.).
    if y <= int(h * 0.135):
        return True

    # Botao de menu no canto superior esquerdo.
    if x <= int(w * 0.09) and y <= int(h * 0.18):
        return True

    # Botao de ajuda no canto inferior esquerdo.
    if x <= int(w * 0.12) and y >= int(h * 0.84):
        return True

    # Botao no canto inferior direito.
    if x >= int(w * 0.88) and y >= int(h * 0.84):
        return True

    return False


def filter_play_area_candidates(candidates, w: int, h: int):
    out = []
    for cand in candidates:
        x, y = cand.point
        if not is_forbidden_ui_point(x, y, w, h):
            out.append(cand)
    return out


def build_plant_obstacle_mask(hsv_img: np.ndarray):
    h, s, v = cv2.split(hsv_img)

    # Folhas verde/ciano
    mask_green = cv2.inRange(h, 32, 100)
    # Flores/miolos rosados
    mask_pink_hi = cv2.inRange(h, 145, 179)
    mask_pink_lo = cv2.inRange(h, 0, 10)
    mask_pink = cv2.bitwise_or(mask_pink_hi, mask_pink_lo)

    mask_color = cv2.bitwise_or(mask_green, mask_pink)
    mask_sat = cv2.inRange(s, 45, 255)
    mask_val = cv2.inRange(v, 35, 255)

    mask = cv2.bitwise_and(mask_color, mask_sat)
    mask = cv2.bitwise_and(mask, mask_val)

    # Agrupa componentes para formar bloco de obstaculo.
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.dilate(mask, np.ones((7, 7), np.uint8), iterations=1)
    return mask


def _line_intersection_ratio(mask: np.ndarray, p0: tuple, p1: tuple):
    h, w = mask.shape[:2]
    x0 = int(np.clip(p0[0], 0, w - 1))
    y0 = int(np.clip(p0[1], 0, h - 1))
    x1 = int(np.clip(p1[0], 0, w - 1))
    y1 = int(np.clip(p1[1], 0, h - 1))

    length = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
    xs = np.linspace(x0, x1, length).astype(np.int32)
    ys = np.linspace(y0, y1, length).astype(np.int32)
    vals = mask[ys, xs]
    return float(np.count_nonzero(vals)) / float(len(vals))


def filter_obstacle_candidates(candidates, obstacle_mask: np.ndarray, fish_point=None):
    if obstacle_mask is None:
        return candidates

    h, w = obstacle_mask.shape[:2]
    filtered = []
    for cand in candidates:
        cx, cy = cand.point
        if cx < 0 or cy < 0 or cx >= w or cy >= h:
            continue

        # Evita clicar muito perto de plantas.
        r = 20
        x0, x1 = max(0, cx - r), min(w, cx + r + 1)
        y0, y1 = max(0, cy - r), min(h, cy + r + 1)
        roi = obstacle_mask[y0:y1, x0:x1]
        if roi.size > 0 and (np.count_nonzero(roi) / float(roi.size)) > 0.12:
            continue

        # Se soubermos onde o peixe esta, evita caminho atravessando obstaculo.
        if fish_point is not None:
            hit_ratio = _line_intersection_ratio(obstacle_mask, fish_point, cand.point)
            if hit_ratio > 0.18:
                continue

        filtered.append(cand)
    return filtered


def detect_other_fish_points(hsv_img: np.ndarray, bgr_img: np.ndarray, player_center: tuple):
    h, s, v = cv2.split(hsv_img)
    mask_sat = cv2.inRange(s, 55, 255)
    mask_val = cv2.inRange(v, 45, 255)
    mask = cv2.bitwise_and(mask_sat, mask_val)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    gray = cv2.cvtColor(bgr_img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 40, 120)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    cx0, cy0 = player_center
    min_self_dist = min(hsv_img.shape[0], hsv_img.shape[1]) * 0.16

    for c in contours:
        area = cv2.contourArea(c)
        if area < 70 or area > 6000:
            continue

        x, y, w, hb = cv2.boundingRect(c)
        if w <= 0 or hb <= 0:
            continue
        aspect = w / float(hb)
        if 0.55 < aspect < 1.8:
            continue

        peri = cv2.arcLength(c, True)
        circularity = 0.0 if peri == 0 else (4 * np.pi * area) / (peri * peri)
        if circularity > 0.55:
            continue

        roi_edges = edges[y : y + hb, x : x + w]
        edge_density = float(np.count_nonzero(roi_edges)) / float(max(1, w * hb))
        if edge_density < 0.012:
            continue

        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        cx = int(m["m10"] / m["m00"])
        cy = int(m["m01"] / m["m00"])

        dist_center = ((cx - cx0) ** 2 + (cy - cy0) ** 2) ** 0.5
        if dist_center < min_self_dist:
            continue

        out.append((cx, cy))

    return out


def _distance_point_to_segment(px, py, ax, ay, bx, by):
    abx = bx - ax
    aby = by - ay
    apx = px - ax
    apy = py - ay
    ab2 = (abx * abx) + (aby * aby)
    if ab2 == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, (apx * abx + apy * aby) / ab2))
    cx = ax + (t * abx)
    cy = ay + (t * aby)
    return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5


def filter_candidates_avoid_other_fish(candidates, player_center: tuple, other_fish_points):
    if not other_fish_points:
        return candidates

    out = []
    ax, ay = player_center
    for cand in candidates:
        bx, by = cand.point
        blocked = False

        for fx, fy in other_fish_points:
            # Evita clicar perto de outros peixes.
            if ((bx - fx) ** 2 + (by - fy) ** 2) ** 0.5 < 70:
                blocked = True
                break

            # Evita rota que passe perto de outros peixes.
            d_line = _distance_point_to_segment(fx, fy, ax, ay, bx, by)
            if d_line < 45:
                blocked = True
                break

        if not blocked:
            out.append(cand)
    return out


def _circular_hue_distance(h1: int, h2: int):
    d = abs(int(h1) - int(h2))
    return min(d, 180 - d)


def detect_goal_hue_top_badge(hsv_img: np.ndarray):
    h, w = hsv_img.shape[:2]
    # Badge de objetivo no topo central (aproximado).
    x0, x1 = int(w * 0.34), int(w * 0.54)
    y0, y1 = int(h * 0.015), int(h * 0.105)
    if x1 <= x0 or y1 <= y0:
        return None

    roi = hsv_img[y0:y1, x0:x1]
    rh, rw = roi.shape[:2]
    if rh <= 0 or rw <= 0:
        return None

    # O icone do objetivo fica na parte esquerda da badge.
    icon_roi = roi[:, : max(1, int(rw * 0.34))]
    hch = icon_roi[:, :, 0]
    sch = icon_roi[:, :, 1]
    vch = icon_roi[:, :, 2]

    # Só considera pixels realmente "vivos" para não confundir com texto/fundo.
    mask_colorful = (sch >= 100) & (vch >= 65)
    colorful_count = int(np.count_nonzero(mask_colorful))
    colorful_ratio = colorful_count / float(icon_roi.shape[0] * icon_roi.shape[1])

    # Se não houver icone forte (ex.: aparece apenas "ZEN KOI 2"), volta modo livre.
    if colorful_count < 60 or colorful_ratio < 0.05:
        return None

    hues = hch[mask_colorful]
    hist = np.bincount(hues.flatten(), minlength=180)
    peak = int(np.argmax(hist))
    return peak


def _candidate_hue(hsv_img: np.ndarray, point: tuple):
    x, y = point
    h, w = hsv_img.shape[:2]
    r = 4
    x0, x1 = max(0, x - r), min(w, x + r + 1)
    y0, y1 = max(0, y - r), min(h, y + r + 1)
    patch = hsv_img[y0:y1, x0:x1]
    if patch.size == 0:
        return None
    sch = patch[:, :, 1]
    hch = patch[:, :, 0]
    mask = sch >= 70
    vals = hch[mask]
    if vals.size == 0:
        vals = hch.flatten()
    return int(np.median(vals))


def filter_candidates_by_goal_hue(candidates, hsv_img: np.ndarray, goal_hue: int, tol: int = 18):
    if goal_hue is None:
        return candidates
    out = []
    for c in candidates:
        ch = _candidate_hue(hsv_img, c.point)
        if ch is None:
            continue
        if _circular_hue_distance(ch, goal_hue) <= tol:
            out.append(c)
    return out


def refine_click_point_on_bait(hsv_img: np.ndarray, target: tuple, goal_hue=None):
    h, w = hsv_img.shape[:2]
    tx, ty = target
    r = 18
    x0, x1 = max(0, tx - r), min(w, tx + r + 1)
    y0, y1 = max(0, ty - r), min(h, ty + r + 1)
    patch = hsv_img[y0:y1, x0:x1]
    if patch.size == 0:
        return target

    ph = patch[:, :, 0].astype(np.float32)
    ps = patch[:, :, 1].astype(np.float32)
    pv = patch[:, :, 2].astype(np.float32)

    mask = (ps >= 70) & (pv >= 55)
    if not np.any(mask):
        return target

    if goal_hue is not None:
        hue_ref = float(goal_hue)
    else:
        hue_ref = float(np.median(ph[mask]))

    hue_dist = np.abs(ph - hue_ref)
    hue_dist = np.minimum(hue_dist, 180.0 - hue_dist)
    hue_score = 1.0 - (hue_dist / 90.0)
    hue_score = np.clip(hue_score, 0.0, 1.0)

    sat_score = ps / 255.0
    val_score = pv / 255.0

    # Prioriza pixel mais "vivo" e de cor compatível com isca/objetivo.
    score = (0.55 * sat_score) + (0.25 * hue_score) + (0.20 * val_score)
    score[~mask] = -1.0

    iy, ix = np.unravel_index(np.argmax(score), score.shape)
    return (x0 + int(ix), y0 + int(iy))


class BaitBandit:
    def __init__(self):
        self.q = defaultdict(float)
        self.n = defaultdict(int)

    def state(self, fish_point, candidate: Candidate):
        if fish_point is None:
            dist_bin = 0
        else:
            fx, fy = fish_point
            cx, cy = candidate.point
            dist = ((cx - fx) ** 2 + (cy - fy) ** 2) ** 0.5
            if dist < 80:
                dist_bin = 0
            elif dist < 160:
                dist_bin = 1
            elif dist < 260:
                dist_bin = 2
            else:
                dist_bin = 3

        area = candidate.area
        if area < 30:
            area_bin = 0
        elif area < 80:
            area_bin = 1
        elif area < 150:
            area_bin = 2
        else:
            area_bin = 3

        return (dist_bin, area_bin)

    def choose(self, fish_point, candidates, epsilon):
        if not candidates:
            return None, None, False

        if random.random() < epsilon:
            c = random.choice(candidates)
            return c, self.state(fish_point, c), True

        scored = []
        for c in candidates:
            s = self.state(fish_point, c)
            scored.append((self.q[s], c, s))
        scored.sort(key=lambda t: t[0], reverse=True)
        _, best_c, best_s = scored[0]
        return best_c, best_s, False

    def update(self, state, reward):
        self.n[state] += 1
        alpha = 1.0 / self.n[state]
        self.q[state] = self.q[state] + alpha * (reward - self.q[state])

    @staticmethod
    def _state_to_key(state):
        return f"{state[0]}|{state[1]}"

    @staticmethod
    def _key_to_state(key: str):
        a, b = key.split("|")
        return int(a), int(b)

    def to_dict(self):
        q_out = {}
        n_out = {}
        keys = set(list(self.q.keys()) + list(self.n.keys()))
        for state in keys:
            k = self._state_to_key(state)
            q_out[k] = float(self.q[state])
            n_out[k] = int(self.n[state])
        return {"q": q_out, "n": n_out}

    def load_dict(self, data: dict):
        self.q = defaultdict(float)
        self.n = defaultdict(int)
        if not data:
            return
        for key, val in data.get("q", {}).items():
            self.q[self._key_to_state(key)] = float(val)
        for key, val in data.get("n", {}).items():
            self.n[self._key_to_state(key)] = int(val)


class ZenKoiBot:
    def __init__(self):
        self.running = False
        self.exit_requested = False
        self.lock = threading.Lock()

        self.target_hwnd = 0
        self.scan_interval = 0.25
        self.chase_wait = 1.5
        self.epsilon = 0.2

        self.fish_spec = None
        self.bait_specs = []
        self.auto_detect_baits = True

        self.on_log = None
        self.on_state = None
        self.on_metrics = None

        self.policy = AdaptiveQPolicy()
        self.reward_engine = RewardEngine()
        self.model_dir = os.path.dirname(os.path.abspath(__file__))
        self.model_store = ModelStore(self.model_dir, MODEL_LATEST_FILE, MODEL_BEST_FILE)
        self.event_logger = EventLogger(self.model_dir)
        self.dataset = DatasetCollector(self.model_dir)
        self.autosave_every_steps = 25
        self.last_autosave_step = 0
        self.best_score = -10**9
        self.lifetime_steps = 0
        self.current_goal_hue = None
        self.no_progress_streak = 0
        self.same_target_streak = 0
        self.last_target = None
        self.click_interval_seconds = 1.0
        self.last_click_ts = 0.0

        self._reset_learning_stats()
        self._load_model()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def _reset_learning_stats(self):
        self.total_steps = 0
        self.total_reward = 0.0
        self.recent_rewards = deque(maxlen=60)
        self.recent_success = deque(maxlen=60)
        self.explore_count = 0
        self.exploit_count = 0
        self.last_reward = 0.0
        self.current_goal_hue = None
        self.no_progress_streak = 0
        self.same_target_streak = 0
        self.last_target = None
        self.last_click_ts = 0.0
        self.episode_started_at = time.time()
        self.episode_consumed = 0
        self.episode_steps = 0
        self.reward_engine.reset()

    def _log(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    def _set_state(self, txt: str):
        if self.on_state:
            self.on_state(txt)

    def _emit_metrics(self):
        if not self.on_metrics:
            return

        recent_avg = 0.0
        if self.recent_rewards:
            recent_avg = sum(self.recent_rewards) / len(self.recent_rewards)

        success_rate = 0.0
        if self.recent_success:
            success_rate = sum(self.recent_success) / len(self.recent_success)

        total_avg = 0.0
        if self.total_steps > 0:
            total_avg = self.total_reward / self.total_steps

        trend = "neutro"
        if len(self.recent_rewards) >= 20:
            half = len(self.recent_rewards) // 2
            first = list(self.recent_rewards)[:half]
            second = list(self.recent_rewards)[half:]
            first_avg = sum(first) / len(first)
            second_avg = sum(second) / len(second)
            diff = second_avg - first_avg
            if diff > 0.08:
                trend = "subindo"
            elif diff < -0.08:
                trend = "caindo"

        metrics = {
            "steps": self.total_steps,
            "total_reward": self.total_reward,
            "recent_avg_reward": recent_avg,
            "total_avg_reward": total_avg,
            "recent_success_rate": success_rate,
            "explore_count": self.explore_count,
            "exploit_count": self.exploit_count,
            "unique_states": len(self.policy.n),
            "last_reward": self.last_reward,
            "trend": trend,
            "best_score": self.best_score,
            "lifetime_steps": self.lifetime_steps,
            "goal_hue": self.current_goal_hue,
            "stall": self.no_progress_streak,
            "same_target_streak": self.same_target_streak,
            "epsilon_live": self.policy.epsilon,
            "episode_consumed": self.episode_consumed,
            "episode_steps": self.episode_steps,
        }
        self.on_metrics(metrics)

    def _current_model_score(self):
        if not self.recent_rewards:
            return -10**9
        recent_avg = sum(self.recent_rewards) / len(self.recent_rewards)
        success_rate = sum(self.recent_success) / len(self.recent_success) if self.recent_success else 0.0
        total_avg = self.total_reward / self.total_steps if self.total_steps > 0 else 0.0
        return (recent_avg * 0.6) + (success_rate * 0.9) + (total_avg * 0.2)

    def _build_model_payload(self, score: float):
        return {
            "version": 1,
            "saved_at": time.time(),
            "best_score": float(self.best_score),
            "candidate_score": float(score),
            "lifetime_steps": int(self.lifetime_steps),
            "policy": self.policy.to_dict(),
        }

    def _write_json(self, path: str, payload: dict):
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)

    def _autosave_model(self, force: bool = False):
        if self.total_steps == 0 and not force:
            return
        if not force and (self.total_steps - self.last_autosave_step) < self.autosave_every_steps:
            return

        score = self._current_model_score()
        payload = self._build_model_payload(score)
        try:
            self.model_store.save_latest(payload)
            self.last_autosave_step = self.total_steps
        except Exception as exc:
            self._log(f"[MODEL] Falha ao salvar latest: {exc}")
            return

        if score >= self.best_score:
            self.best_score = score
            payload["best_score"] = float(self.best_score)
            try:
                self.model_store.save_best(payload)
                self._log(f"[MODEL] Novo melhor modelo salvo (score={score:.3f}).")
            except Exception as exc:
                self._log(f"[MODEL] Falha ao salvar best: {exc}")

    def _payload_score(self, payload: dict):
        if not payload:
            return -10**9
        # Usa o maior entre candidate_score e best_score para comparar modelos.
        s_best = float(payload.get("best_score", -10**9))
        s_cand = float(payload.get("candidate_score", -10**9))
        return max(s_best, s_cand)

    def _load_payload(self, payload: dict):
        self.bandit.load_dict(payload.get("bandit", {}))
        self.best_score = float(payload.get("best_score", self.best_score))
        self.lifetime_steps = int(payload.get("lifetime_steps", self.lifetime_steps))

    def _load_model(self):
        snap = self.model_store.load_best_available()
        if snap is None:
            self._log("[MODEL] Sem modelo salvo anterior. Iniciando novo aprendizado.")
            return
        payload = snap.payload
        pol = payload.get("policy", payload.get("bandit", {}))
        self.policy.load_dict(pol)
        self.best_score = float(payload.get("best_score", self.best_score))
        self.lifetime_steps = int(payload.get("lifetime_steps", self.lifetime_steps))
        self._log(f"[MODEL] Modelo {snap.source} carregado (score={snap.score:.3f}).")

    def configure(self, hwnd: int, scan_interval: float, chase_wait: float, epsilon: float, auto_detect_baits: bool):
        self.target_hwnd = hwnd
        self.scan_interval = scan_interval
        self.chase_wait = chase_wait
        self.epsilon = epsilon
        self.auto_detect_baits = auto_detect_baits

    def start(self):
        with self.lock:
            self.running = True
        # Ao iniciar, recarrega o maior score salvo para sempre começar do melhor estado conhecido.
        self._load_model()
        self._reset_learning_stats()
        self.last_autosave_step = 0
        self._emit_metrics()
        self._set_state("rodando")
        self._log("Bot iniciado.")

    def stop(self):
        with self.lock:
            self.running = False
        self._autosave_model(force=True)
        self._set_state("parado")
        self._log("Bot pausado.")

    def shutdown(self):
        self.exit_requested = True
        self._autosave_model(force=True)
        self.stop()
        self.thread.join(timeout=1)

    def loop(self):
        while not self.exit_requested:
            with self.lock:
                run = self.running

            if not run:
                time.sleep(0.05)
                continue

            if not self.target_hwnd or not win32gui.IsWindow(self.target_hwnd):
                self._log("Janela alvo invalida. Pausando.")
                self.stop()
                continue

            if not self.auto_detect_baits and not self.bait_specs:
                self._log("Calibre ao menos um tipo de isca antes de iniciar.")
                self.stop()
                continue

            frame_bgr, _ = capture_client_bgr(self.target_hwnd)
            if frame_bgr is None:
                self._log("Falha ao capturar janela. Pausando.")
                self.stop()
                continue

            hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
            plant_mask = build_plant_obstacle_mask(hsv)
            self.current_goal_hue = detect_goal_hue_top_badge(hsv)
            fh, fw = frame_bgr.shape[:2]
            player_center = (fw // 2, fh // 2)
            other_fish_points = detect_other_fish_points(hsv, frame_bgr, player_center)

            fish_point = player_center

            if self.auto_detect_baits:
                bait_cands = detect_candidates_auto(hsv, frame_bgr, min_area=14, max_area=2500)
            else:
                bait_cands = detect_candidates_multi(hsv, self.bait_specs, min_area=8, max_area=9000)
            bait_cands = filter_play_area_candidates(bait_cands, fw, fh)
            bait_cands = filter_obstacle_candidates(bait_cands, plant_mask, fish_point=fish_point)
            bait_cands = filter_candidates_avoid_other_fish(bait_cands, player_center, other_fish_points)
            goal_filtered = filter_candidates_by_goal_hue(bait_cands, hsv, self.current_goal_hue, tol=28)
            if goal_filtered:
                bait_cands = goal_filtered
            if not bait_cands:
                time.sleep(self.scan_interval)
                continue

            feature_map = {}
            for idx, cand in enumerate(bait_cands):
                cx, cy = cand.point
                local_risk = 0.0
                rr = 18
                x0, x1 = max(0, cx - rr), min(fw, cx + rr + 1)
                y0, y1 = max(0, cy - rr), min(fh, cy + rr + 1)
                roi = plant_mask[y0:y1, x0:x1]
                if roi.size > 0:
                    local_risk = float(np.count_nonzero(roi)) / float(roi.size)

                min_other = 9999.0
                for ox, oy in other_fish_points:
                    d = ((ox - cx) ** 2 + (oy - cy) ** 2) ** 0.5
                    if d < min_other:
                        min_other = d
                if min_other < 9999:
                    local_risk = max(local_risk, max(0.0, min(1.0, (90.0 - min_other) / 90.0)))

                ctx = TemporalContext(
                    fish_point=fish_point,
                    last_target=self.last_target,
                    no_progress_streak=self.no_progress_streak,
                    goal_hue=self.current_goal_hue,
                )
                bins = bins_from_candidate(cand, ctx, local_risk)
                feature_map[idx] = CandidateFeatures(
                    dist_bin=bins["dist_bin"],
                    area_bin=bins["area_bin"],
                    risk_bin=bins["risk_bin"],
                    goal_bin=bins["goal_bin"],
                    angle_bin=bins["angle_bin"],
                    stall_bin=bins["stall_bin"],
                )

            effective_epsilon = min(0.80, self.epsilon + (0.10 * self.no_progress_streak))
            force_explore = self.no_progress_streak >= 4
            chosen_idx, state, used_explore = self.policy.choose(
                feature_map,
                force_explore=force_explore,
                epsilon_override=effective_epsilon,
            )
            if chosen_idx is None:
                time.sleep(self.scan_interval)
                continue
            chosen = bait_cands[chosen_idx]

            # Anti-stall: se travou por varios ciclos, força trocar para alvo distante do ultimo.
            if self.no_progress_streak >= 4 and self.last_target is not None and len(bait_cands) > 1:
                lx, ly = self.last_target
                chosen = max(
                    bait_cands,
                    key=lambda c: ((c.point[0] - lx) ** 2 + (c.point[1] - ly) ** 2),
                )
                b = bins_from_candidate(
                    chosen,
                    TemporalContext(
                        fish_point=fish_point,
                        last_target=self.last_target,
                        no_progress_streak=self.no_progress_streak,
                        goal_hue=self.current_goal_hue,
                    ),
                    0.5,
                )
                state = self.policy.state_tuple(
                    CandidateFeatures(
                        dist_bin=b["dist_bin"],
                        area_bin=b["area_bin"],
                        risk_bin=b["risk_bin"],
                        goal_bin=b["goal_bin"],
                        angle_bin=b["angle_bin"],
                        stall_bin=b["stall_bin"],
                    )
                )
                used_explore = True

            target = refine_click_point_on_bait(hsv, chosen.point, goal_hue=self.current_goal_hue)
            now = time.time()
            wait_left = self.click_interval_seconds - (now - self.last_click_ts)
            if wait_left > 0:
                time.sleep(wait_left)
            send_client_click(self.target_hwnd, target[0], target[1])
            self.last_click_ts = time.time()

            fish_dist_before = None

            click_t0 = time.time()
            time.sleep(self.chase_wait)

            after_bgr, _ = capture_client_bgr(self.target_hwnd)
            if after_bgr is None:
                time.sleep(self.scan_interval)
                continue

            after_hsv = cv2.cvtColor(after_bgr, cv2.COLOR_BGR2HSV)
            if self.auto_detect_baits:
                bait_after = detect_candidates_auto(after_hsv, after_bgr, min_area=14, max_area=2500)
            else:
                bait_after = detect_candidates_multi(after_hsv, self.bait_specs, min_area=8, max_area=9000)
            fish_after_point = None

            target_still_exists = point_exists_near(bait_after, target, radius=26)
            consumed = not target_still_exists
            elapsed_after_click = time.time() - click_t0
            reward, self.no_progress_streak, self.same_target_streak = self.reward_engine.compute(
                target,
                consumed=consumed,
                elapsed_after_click=elapsed_after_click,
            )
            self.last_target = self.reward_engine.last_target

            if fish_dist_before is not None and fish_after_point is not None:
                fx2, fy2 = fish_after_point
                tx, ty = target
                fish_dist_after = ((fx2 - tx) ** 2 + (fy2 - ty) ** 2) ** 0.5
                if fish_dist_after < fish_dist_before - 12:
                    reward += 0.4
                else:
                    reward -= 0.1

            self.policy.update(state, reward)
            self.total_steps += 1
            self.lifetime_steps += 1
            self.total_reward += reward
            self.last_reward = reward
            self.recent_rewards.append(reward)
            self.recent_success.append(1 if consumed else 0)
            self.episode_steps += 1
            if consumed:
                self.episode_consumed += 1
            if used_explore:
                self.explore_count += 1
            else:
                self.exploit_count += 1
            self.event_logger.log(
                "step",
                {
                    "target": target,
                    "reward": reward,
                    "consumed": consumed,
                    "epsilon": effective_epsilon,
                    "stall": self.no_progress_streak,
                    "state": list(state) if state is not None else None,
                },
            )
            self.dataset.save_patch(after_bgr, target, success=consumed)
            self._emit_metrics()
            self._autosave_model(force=False)
            self._log(
                f"Clique em {target} | reward={reward:.2f} | estado={state} | eps={effective_epsilon:.2f} | stall={self.no_progress_streak}"
            )

            time.sleep(self.scan_interval)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Zen Koi 2 RL Bot")
        self.root.geometry("860x650")
        self.root.resizable(False, False)

        self.bot = ZenKoiBot()
        self.bot.on_log = self.log
        self.bot.on_state = self.set_state
        self.bot.on_metrics = self.update_metrics

        self.windows = []
        self.selected_hwnd = 0
        self.capturing = False
        self.fish_sample_hsv = None

        self._build_ui()
        self.refresh_windows()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.pack(fill="x")

        self.topmost_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Sempre no topo", variable=self.topmost_var, command=self.toggle_topmost).pack(side="left")
        ttk.Label(top, text="Modo automatico: apenas Iniciar e Parar").pack(side="left", padx=(16, 0))

        ttk.Label(main, text="Janela do jogo:").pack(anchor="w", pady=(8, 0))
        row_pick = ttk.Frame(main)
        row_pick.pack(fill="x", pady=(2, 8))

        self.cmb = ttk.Combobox(row_pick, state="readonly")
        self.cmb.pack(side="left", fill="x", expand=True)
        self.cmb.bind("<<ComboboxSelected>>", self.on_window_selected)

        ttk.Button(row_pick, text="Atualizar", command=self.refresh_windows).pack(side="left", padx=(8, 0))

        row_cfg = ttk.Frame(main)
        row_cfg.pack(fill="x", pady=(4, 8))

        ttk.Label(row_cfg, text="Scan(s):").pack(side="left")
        self.e_scan = ttk.Entry(row_cfg, width=8)
        self.e_scan.insert(0, "0.25")
        self.e_scan.pack(side="left", padx=(4, 10))

        ttk.Label(row_cfg, text="Espera apos clique(s):").pack(side="left")
        self.e_chase = ttk.Entry(row_cfg, width=8)
        self.e_chase.insert(0, "1.5")
        self.e_chase.pack(side="left", padx=(4, 10))

        ttk.Label(row_cfg, text="Epsilon:").pack(side="left")
        self.e_eps = ttk.Entry(row_cfg, width=8)
        self.e_eps.insert(0, "0.20")
        self.e_eps.pack(side="left", padx=(4, 10))

        self.auto_baits_var = tk.BooleanVar(value=True)
        self.chk_auto = ttk.Checkbutton(
            row_cfg,
            text="Auto detectar iscas (sempre ligado)",
            variable=self.auto_baits_var,
        )
        self.chk_auto.state(["selected", "disabled"])
        self.chk_auto.pack(side="left", padx=(8, 0))

        row_sample = ttk.Frame(main)
        row_sample.pack(fill="x", pady=(2, 8))

        ttk.Button(row_sample, text="Amostra Peixe (opcional)", command=self.sample_fish).pack(side="left")
        ttk.Button(row_sample, text="Adicionar isca (opcional)", command=self.sample_bait).pack(side="left", padx=(8, 0))
        ttk.Button(row_sample, text="Desfazer ultima", command=self.remove_last_bait).pack(side="left", padx=(8, 0))
        ttk.Button(row_sample, text="Limpar iscas", command=self.clear_baits).pack(side="left", padx=(8, 0))
        ttk.Button(row_sample, text="Testar 1 clique", command=self.test_click).pack(side="left", padx=(8, 0))

        self.lbl_fish = ttk.Label(row_sample, text="Peixe: nao calibrado")
        self.lbl_fish.pack(side="left", padx=(16, 0))

        self.lbl_bait = ttk.Label(row_sample, text="Iscas: 0")
        self.lbl_bait.pack(side="left", padx=(16, 0))

        samples_box = ttk.LabelFrame(main, text="Amostras Cadastradas", padding=8)
        samples_box.pack(fill="x", pady=(2, 8))
        self.samples_txt = tk.Text(samples_box, width=110, height=5, state="disabled")
        self.samples_txt.pack(fill="x")

        row_run = ttk.Frame(main)
        row_run.pack(fill="x", pady=(2, 8))

        self.btn_start = ttk.Button(row_run, text="Iniciar", command=self.start)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(row_run, text="Parar", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=(8, 0))

        self.state_var = tk.StringVar(value="Status: parado")
        ttk.Label(row_run, textvariable=self.state_var).pack(side="left", padx=(16, 0))

        metrics_box = ttk.LabelFrame(main, text="Aprendizado (tempo real)", padding=8)
        metrics_box.pack(fill="x", pady=(2, 8))

        self.m_steps = tk.StringVar(value="Passos: 0")
        self.m_last = tk.StringVar(value="Ultimo reward: 0.00")
        self.m_recent = tk.StringVar(value="Reward medio recente: 0.00")
        self.m_total = tk.StringVar(value="Reward medio total: 0.00")
        self.m_success = tk.StringVar(value="Sucesso recente: 0.0%")
        self.m_explore = tk.StringVar(value="Explorar/Explotar: 0/0")
        self.m_states = tk.StringVar(value="Estados aprendidos: 0")
        self.m_trend = tk.StringVar(value="Tendencia: neutro")
        self.m_lifetime = tk.StringVar(value="Passos vitais: 0")
        self.m_best = tk.StringVar(value="Melhor score salvo: n/a")
        self.m_goal = tk.StringVar(value="Objetivo hue: n/a")
        self.m_stall = tk.StringVar(value="Stall: 0 (ok)")

        ttk.Label(metrics_box, textvariable=self.m_steps).grid(row=0, column=0, sticky="w", padx=(0, 20))
        ttk.Label(metrics_box, textvariable=self.m_last).grid(row=0, column=1, sticky="w", padx=(0, 20))
        ttk.Label(metrics_box, textvariable=self.m_recent).grid(row=0, column=2, sticky="w")
        ttk.Label(metrics_box, textvariable=self.m_total).grid(row=1, column=0, sticky="w", padx=(0, 20))
        ttk.Label(metrics_box, textvariable=self.m_success).grid(row=1, column=1, sticky="w", padx=(0, 20))
        ttk.Label(metrics_box, textvariable=self.m_explore).grid(row=1, column=2, sticky="w")
        ttk.Label(metrics_box, textvariable=self.m_states).grid(row=2, column=0, sticky="w", padx=(0, 20))
        ttk.Label(metrics_box, textvariable=self.m_trend).grid(row=2, column=1, sticky="w", padx=(0, 20))
        ttk.Label(metrics_box, textvariable=self.m_lifetime).grid(row=2, column=2, sticky="w")
        ttk.Label(metrics_box, textvariable=self.m_best).grid(row=3, column=0, sticky="w", padx=(0, 20))
        ttk.Label(metrics_box, textvariable=self.m_goal).grid(row=3, column=1, sticky="w", padx=(0, 20))
        self.lbl_stall = tk.Label(metrics_box, textvariable=self.m_stall, fg="#1E8E3E")
        self.lbl_stall.grid(row=3, column=2, sticky="w")

        ttk.Label(main, text="Log:").pack(anchor="w")
        self.txt = tk.Text(main, width=110, height=20, state="disabled")
        self.txt.pack(fill="both", expand=True)

        self.log("Selecione a janela do Zen Koi 2. IA usa auto-deteccao de iscas por padrao (sem cadastro manual).")
        self._refresh_samples_view()
        self.toggle_topmost()

    def toggle_topmost(self):
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

    def log(self, msg: str):
        self.root.after(0, self._log_ui, msg)

    def _log_ui(self, msg: str):
        self.txt.config(state="normal")
        self.txt.insert("end", f"{time.strftime('%H:%M:%S')} - {msg}\n")
        self.txt.see("end")
        self.txt.config(state="disabled")

    def set_state(self, status: str):
        self.root.after(0, self._set_state_ui, status)

    def _set_state_ui(self, status: str):
        self.state_var.set(f"Status: {status}")
        running = status == "rodando"
        self.btn_start.config(state="disabled" if running else "normal")
        self.btn_stop.config(state="normal" if running else "disabled")

    def update_metrics(self, metrics: dict):
        self.root.after(0, self._update_metrics_ui, metrics)

    def _update_metrics_ui(self, metrics: dict):
        self.m_steps.set(f"Passos: {metrics['steps']}")
        self.m_last.set(f"Ultimo reward: {metrics['last_reward']:.2f}")
        self.m_recent.set(f"Reward medio recente: {metrics['recent_avg_reward']:.2f}")
        self.m_total.set(f"Reward medio total: {metrics['total_avg_reward']:.2f}")
        self.m_success.set(f"Sucesso recente: {metrics['recent_success_rate'] * 100:.1f}%")
        self.m_explore.set(f"Explorar/Explotar: {metrics['explore_count']}/{metrics['exploit_count']}")
        self.m_states.set(f"Estados aprendidos: {metrics['unique_states']}")
        self.m_trend.set(f"Tendencia: {metrics['trend']}")
        self.m_lifetime.set(f"Passos vitais: {metrics['lifetime_steps']}")
        self.m_best.set(f"Melhor score salvo: {metrics['best_score']:.3f}")
        goal_hue = metrics.get("goal_hue")
        self.m_goal.set(f"Objetivo hue: {goal_hue if goal_hue is not None else 'n/a'}")
        stall = int(metrics.get("stall", 0))
        same = int(metrics.get("same_target_streak", 0))
        if stall <= 1:
            self.lbl_stall.config(fg="#1E8E3E")
            self.m_stall.set(f"Stall: {stall} (ok)")
        elif stall <= 3:
            self.lbl_stall.config(fg="#D99600")
            self.m_stall.set(f"Stall: {stall} (alerta)")
        else:
            self.lbl_stall.config(fg="#C62828")
            self.m_stall.set(f"Stall: {stall} (travado, same={same})")

    def refresh_windows(self):
        prev = self.selected_hwnd
        self.windows = list_selectable_windows()
        self.cmb["values"] = [w.display for w in self.windows]

        if not self.windows:
            self.cmb.set("")
            self.selected_hwnd = 0
            self.log("Nenhuma janela encontrada.")
            return

        idx = 0
        for i, w in enumerate(self.windows):
            if w.hwnd == prev:
                idx = i
                break
        if prev == 0:
            for i, w in enumerate(self.windows):
                if AUTO_TARGET_HINT in w.title.lower():
                    idx = i
                    break

        self.cmb.current(idx)
        self.on_window_selected()
        self.log(f"Lista atualizada: {len(self.windows)} janela(s). Alvo automatico: {self.windows[idx].title}")

    def on_window_selected(self, _event=None):
        idx = self.cmb.current()
        if idx < 0 or idx >= len(self.windows):
            self.selected_hwnd = 0
            return
        w = self.windows[idx]
        self.selected_hwnd = w.hwnd
        self.log(f"Selecionado: {w.title} ({w.process} | PID {w.pid})")

    def read_cfg(self):
        try:
            scan = float(self.e_scan.get().strip())
            chase = float(self.e_chase.get().strip())
            eps = float(self.e_eps.get().strip())
        except ValueError:
            messagebox.showerror("Erro", "Scan, espera e epsilon devem ser numeros.")
            return None

        if scan <= 0 or chase <= 0:
            messagebox.showerror("Erro", "Scan e espera precisam ser maiores que zero.")
            return None

        if eps < 0 or eps > 1:
            messagebox.showerror("Erro", "Epsilon deve estar entre 0 e 1.")
            return None

        return scan, chase, eps

    def bring_window(self, hwnd: int):
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass

    def sample_color_click(self, label: str):
        if self.capturing:
            return None
        if not self.selected_hwnd or not win32gui.IsWindow(self.selected_hwnd):
            messagebox.showerror("Erro", "Selecione uma janela valida antes de calibrar.")
            return None

        self.capturing = True
        self.log(f"{label}: clique no jogo no pixel desejado (20s).")
        self.bring_window(self.selected_hwnd)

        deadline = time.time() + 20
        was_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)

        while time.time() < deadline:
            now_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
            if now_down and not was_down:
                pos = win32api.GetCursorPos()
                clicked_hwnd = win32gui.WindowFromPoint(pos)
                inside = clicked_hwnd == self.selected_hwnd or win32gui.IsChild(self.selected_hwnd, clicked_hwnd)
                if inside:
                    frame_bgr, (sx, sy) = capture_client_bgr(self.selected_hwnd)
                    if frame_bgr is None:
                        self.capturing = False
                        return None
                    cx = pos[0] - sx
                    cy = pos[1] - sy
                    h, w, _ = frame_bgr.shape
                    if cx < 0 or cy < 0 or cx >= w or cy >= h:
                        self.capturing = False
                        return None
                    b, g, r = frame_bgr[cy, cx]
                    hsv = cv2.cvtColor(np.uint8([[[b, g, r]]]), cv2.COLOR_BGR2HSV)[0][0]
                    self.capturing = False
                    return int(hsv[0]), int(hsv[1]), int(hsv[2])

                self.log("Clique fora da janela alvo. Tente de novo.")

            was_down = now_down
            self.root.update_idletasks()
            self.root.update()
            time.sleep(0.01)

        self.capturing = False
        return None

    def sample_fish(self):
        hsv = self.sample_color_click("Amostra Peixe")
        if hsv is None:
            messagebox.showerror("Erro", "Falha ou timeout ao capturar amostra do peixe.")
            return
        self.bot.fish_spec = build_color_spec(hsv, h_tol=12, s_tol=80, v_tol=80)
        self.fish_sample_hsv = hsv
        self.lbl_fish.config(text=f"Peixe HSV: {hsv}")
        self._refresh_samples_view()
        self.log(f"Peixe calibrado em HSV={hsv}")

    def sample_bait(self):
        hsv = self.sample_color_click("Adicionar Isca")
        if hsv is None:
            messagebox.showerror("Erro", "Falha ou timeout ao capturar amostra da isca.")
            return
        self.bot.bait_specs.append(build_color_spec(hsv, h_tol=10, s_tol=90, v_tol=90))
        self.lbl_bait.config(text=f"Iscas: {len(self.bot.bait_specs)} (ultima HSV: {hsv})")
        self._refresh_samples_view()
        self.log(f"Isca adicionada em HSV={hsv}. Total: {len(self.bot.bait_specs)}")

    def remove_last_bait(self):
        if not self.bot.bait_specs:
            self.log("Nao ha iscas para remover.")
            return
        self.bot.bait_specs.pop()
        self.lbl_bait.config(text=f"Iscas: {len(self.bot.bait_specs)}")
        self._refresh_samples_view()
        self.log(f"Ultima isca removida. Total: {len(self.bot.bait_specs)}")

    def clear_baits(self):
        self.bot.bait_specs = []
        self.lbl_bait.config(text="Iscas: 0")
        self._refresh_samples_view()
        self.log("Lista de iscas limpa.")

    def _refresh_samples_view(self):
        fish_line = "Peixe: nao cadastrado" if self.fish_sample_hsv is None else f"Peixe: HSV={self.fish_sample_hsv}"
        bait_lines = []
        for i, spec in enumerate(self.bot.bait_specs, start=1):
            bait_lines.append(f"Isca {i}: HSV={tuple(int(v) for v in spec.sample_hsv)}")
        if not bait_lines:
            bait_lines = ["Iscas: nenhuma cadastrada"]

        text = fish_line + "\n" + "\n".join(bait_lines)
        self.samples_txt.config(state="normal")
        self.samples_txt.delete("1.0", "end")
        self.samples_txt.insert("end", text)
        self.samples_txt.config(state="disabled")

    def test_click(self):
        if not self.selected_hwnd or not win32gui.IsWindow(self.selected_hwnd):
            messagebox.showerror("Erro", "Selecione uma janela valida.")
            return

        auto_mode = bool(self.auto_baits_var.get())
        if not auto_mode and not self.bot.bait_specs:
            messagebox.showerror("Erro", "Adicione ao menos um tipo de isca antes de testar clique.")
            return

        frame_bgr, _ = capture_client_bgr(self.selected_hwnd)
        if frame_bgr is None:
            messagebox.showerror("Erro", "Nao consegui capturar a janela.")
            return

        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        plant_mask = build_plant_obstacle_mask(hsv)
        if auto_mode:
            baits = detect_candidates_auto(hsv, frame_bgr, min_area=14, max_area=2500)
        else:
            baits = detect_candidates_multi(hsv, self.bot.bait_specs, min_area=8, max_area=9000)
        fh, fw = frame_bgr.shape[:2]
        baits = filter_play_area_candidates(baits, fw, fh)
        baits = filter_obstacle_candidates(baits, plant_mask, fish_point=None)
        if not baits:
            messagebox.showerror("Erro", "Nenhuma isca boa detectada (fora de UI/plantas) no frame atual.")
            return

        target = max(baits, key=lambda c: c.area).point
        send_client_click(self.selected_hwnd, target[0], target[1])
        self.log(f"Teste: clique enviado para isca detectada em {target}.")

    def start(self):
        # Fluxo simplificado: um clique em iniciar deve bastar.
        self.refresh_windows()
        if not self.selected_hwnd or not win32gui.IsWindow(self.selected_hwnd):
            messagebox.showerror("Erro", "Nao encontrei janela do Zen Koi aberta.")
            return

        self.auto_baits_var.set(True)
        self.bot.configure(self.selected_hwnd, DEFAULT_SCAN, DEFAULT_CHASE, DEFAULT_EPS, True)
        self.log(
            f"Start automatico: janela={self.windows[self.cmb.current()].title} "
            f"| scan={DEFAULT_SCAN}s | chase={DEFAULT_CHASE}s | eps={DEFAULT_EPS}"
        )
        self.bot.start()

    def stop(self):
        self.bot.stop()

    def on_close(self):
        self.bot.shutdown()
        self.root.destroy()


def main():
    root = tk.Tk()
    ttk.Style().theme_use("clam")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()









