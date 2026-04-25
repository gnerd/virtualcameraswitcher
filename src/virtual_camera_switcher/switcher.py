import logging

from .config import CameraCalibration

logger = logging.getLogger(__name__)


class CameraSwitcher:
    """Determines which camera to switch to based on yaw angle with hysteresis."""

    def __init__(self, calibrations: list[CameraCalibration], hysteresis_frames: int = 10):
        self._calibrations = sorted(calibrations, key=lambda c: c.yaw_center)
        self._hysteresis = hysteresis_frames
        self._current_index: int | None = None
        self._candidate_index: int | None = None
        self._candidate_count: int = 0

    @property
    def current_camera_index(self) -> int | None:
        return self._current_index

    def update(self, yaw: float | None) -> int | None:
        """Given a yaw angle, return the camera index that should be active.
        Returns None if no face is detected (keeps current camera)."""
        if yaw is None or not self._calibrations:
            self._candidate_count = 0
            return self._current_index

        # Find nearest calibrated camera
        best = min(self._calibrations, key=lambda c: abs(c.yaw_center - yaw))
        target_index = best.camera_index

        if target_index == self._current_index:
            self._candidate_count = 0
            self._candidate_index = None
            return self._current_index

        # Hysteresis: must consistently point at new camera for N frames
        if target_index == self._candidate_index:
            self._candidate_count += 1
        else:
            self._candidate_index = target_index
            self._candidate_count = 1

        if self._candidate_count >= self._hysteresis:
            old = self._current_index
            self._current_index = target_index
            self._candidate_count = 0
            self._candidate_index = None
            logger.info("Switched camera: %s -> %s", old, self._current_index)
            return self._current_index

        return self._current_index
