import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class SwitcherState(Enum):
    LOCKED = "locked"
    SEARCHING = "searching"


class GazeSwitcher:
    """State-machine switcher.

    LOCKED: We trust the active camera. Detection runs only on it, slowly.
        If the person looks away (yaw exceeds threshold or no face found) for
        `look_away_grace_s` seconds, we transition to SEARCHING.

    SEARCHING: We sweep the other cameras at a higher rate. The first camera
        whose yaw is within the look-away threshold (and beats the active by
        `switch_margin`) wins. If nothing better is found within
        `search_timeout_s`, we stay on the current camera and re-arm.

    The coordinator calls `next_targets()` to learn which cameras to detect on
    and how long to sleep, then feeds results back via `submit()`.
    """

    def __init__(
        self,
        camera_indices: list[int],
        active_index: int,
        *,
        locked_fps: float = 4.0,
        searching_fps: float = 10.0,
        look_away_yaw_deg: float = 25.0,
        look_away_grace_s: float = 1.0,
        search_timeout_s: float = 3.0,
        switch_margin: float = 5.0,
    ):
        self._cameras = list(camera_indices)
        self._active = active_index
        self._state = SwitcherState.LOCKED
        self._locked_period = 1.0 / max(locked_fps, 0.1)
        self._searching_period = 1.0 / max(searching_fps, 0.1)
        self._look_away_yaw = look_away_yaw_deg
        self._look_away_grace = look_away_grace_s
        self._search_timeout = search_timeout_s
        self._switch_margin = switch_margin

        self._away_since: float | None = None
        self._search_since: float | None = None
        self._search_results: dict[int, float] = {}

    @property
    def active(self) -> int:
        return self._active

    @property
    def state(self) -> SwitcherState:
        return self._state

    def next_targets(self) -> tuple[list[int], float]:
        """Returns (cameras_to_detect_this_tick, sleep_seconds_after)."""
        if self._state is SwitcherState.LOCKED:
            return [self._active], self._locked_period

        # SEARCHING: scan cameras we haven't seen yet this round (skip active).
        pending = [
            c for c in self._cameras
            if c != self._active and c not in self._search_results
        ]
        if not pending:
            # All scanned; loop again until decision/timeout fires.
            pending = [c for c in self._cameras if c != self._active]
        return pending, self._searching_period

    def submit(self, results: dict[int, float | None]) -> int | None:
        """Feed in detection results from the most recent tick.
        Returns the new active camera index iff a switch should happen now."""
        now = time.monotonic()

        if self._state is SwitcherState.LOCKED:
            yaw = results.get(self._active)
            looking_away = yaw is None or abs(yaw) > self._look_away_yaw
            if looking_away:
                if self._away_since is None:
                    self._away_since = now
                elif now - self._away_since >= self._look_away_grace:
                    logger.info(
                        "Camera %s lost gaze (yaw=%s); entering SEARCHING",
                        self._active,
                        f"{yaw:+.1f}" if yaw is not None else "no-face",
                    )
                    self._state = SwitcherState.SEARCHING
                    self._search_since = now
                    self._search_results.clear()
                    self._away_since = None
            else:
                self._away_since = None
            return None

        # SEARCHING
        for idx, yaw in results.items():
            if yaw is not None:
                self._search_results[idx] = yaw

        scanned_all = all(
            c == self._active or c in self._search_results
            for c in self._cameras
        )
        timed_out = (now - (self._search_since or now)) >= self._search_timeout
        if not (scanned_all or timed_out):
            return None

        # Pick the most front-facing candidate that's actually looking forward.
        candidates = {
            idx: yaw for idx, yaw in self._search_results.items()
            if abs(yaw) <= self._look_away_yaw
        }
        new_active: int | None = None
        if candidates:
            best = min(candidates, key=lambda i: abs(candidates[i]))
            new_active = best
            logger.info(
                "Search complete: switching %s -> %s (yaw=%+.1f)",
                self._active, best, candidates[best],
            )
        else:
            logger.info(
                "Search ended with no better camera; staying on %s",
                self._active,
            )

        self._state = SwitcherState.LOCKED
        self._search_results.clear()
        self._search_since = None
        # Re-arm: don't immediately re-trigger if the active camera is still bad.
        self._away_since = now

        if new_active is not None and new_active != self._active:
            self._active = new_active
            self._away_since = None
            return self._active
        return None
