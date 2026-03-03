class RewardEngine:
    def __init__(self):
        self.no_progress_streak = 0
        self.same_target_streak = 0
        self.last_target = None

    def reset(self):
        self.no_progress_streak = 0
        self.same_target_streak = 0
        self.last_target = None

    def compute(self, target, consumed, elapsed_after_click):
        reward = -0.2

        if consumed:
            reward += 2.0
            reward += max(0.0, 1.0 - (elapsed_after_click * 0.5))
            self.no_progress_streak = 0
            self.same_target_streak = 0
            self.last_target = None
        else:
            self.no_progress_streak += 1

        if self.no_progress_streak > 0:
            reward -= min(1.5, 0.20 * self.no_progress_streak)

        if self.last_target is not None:
            lx, ly = self.last_target
            tx, ty = target
            if ((lx - tx) ** 2 + (ly - ty) ** 2) ** 0.5 <= 14:
                self.same_target_streak += 1
            else:
                self.same_target_streak = 0
        else:
            self.same_target_streak = 0

        self.last_target = target

        if self.same_target_streak > 1:
            reward -= min(0.8, 0.16 * self.same_target_streak)

        return reward, self.no_progress_streak, self.same_target_streak
