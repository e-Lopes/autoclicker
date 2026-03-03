import math
from dataclasses import dataclass


@dataclass
class TemporalContext:
    fish_point: tuple
    last_target: tuple | None
    no_progress_streak: int
    goal_hue: int | None


def angle_bin_from_vectors(v1, v2):
    if v1 is None or v2 is None:
        return 0
    x1, y1 = v1
    x2, y2 = v2
    n1 = (x1 * x1 + y1 * y1) ** 0.5
    n2 = (x2 * x2 + y2 * y2) ** 0.5
    if n1 == 0 or n2 == 0:
        return 0
    c = max(-1.0, min(1.0, (x1 * x2 + y1 * y2) / (n1 * n2)))
    ang = math.degrees(math.acos(c))
    if ang < 30:
        return 0
    if ang < 70:
        return 1
    if ang < 120:
        return 2
    return 3


def bins_from_candidate(candidate, ctx: TemporalContext, risk_score: float):
    cx, cy = candidate.point
    if ctx.fish_point is None:
        dist = 0.0
    else:
        fx, fy = ctx.fish_point
        dist = ((cx - fx) ** 2 + (cy - fy) ** 2) ** 0.5

    if dist < 90:
        dist_bin = 0
    elif dist < 170:
        dist_bin = 1
    elif dist < 280:
        dist_bin = 2
    else:
        dist_bin = 3

    area = candidate.area
    if area < 24:
        area_bin = 0
    elif area < 70:
        area_bin = 1
    elif area < 160:
        area_bin = 2
    else:
        area_bin = 3

    if risk_score < 0.15:
        risk_bin = 0
    elif risk_score < 0.35:
        risk_bin = 1
    elif risk_score < 0.55:
        risk_bin = 2
    else:
        risk_bin = 3

    goal_bin = 1 if ctx.goal_hue is not None else 0

    if ctx.last_target is None:
        angle_bin = 0
    else:
        fx, fy = ctx.fish_point
        lx, ly = ctx.last_target
        v_prev = (lx - fx, ly - fy)
        v_cur = (cx - fx, cy - fy)
        angle_bin = angle_bin_from_vectors(v_prev, v_cur)

    if ctx.no_progress_streak <= 1:
        stall_bin = 0
    elif ctx.no_progress_streak <= 3:
        stall_bin = 1
    elif ctx.no_progress_streak <= 6:
        stall_bin = 2
    else:
        stall_bin = 3

    return {
        "dist_bin": dist_bin,
        "area_bin": area_bin,
        "risk_bin": risk_bin,
        "goal_bin": goal_bin,
        "angle_bin": angle_bin,
        "stall_bin": stall_bin,
    }
