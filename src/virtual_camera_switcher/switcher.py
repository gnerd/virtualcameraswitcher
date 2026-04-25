import logging

logger = logging.getLogger(__name__)


class CameraSwitcher:
    """Picks the camera where the face is most front-facing, with hysteresis."""

    def __init__(self, camera_indices: list[int], hysteresis_frames: int = 10):
        self._camera_indices = camera_indices
        self._hysteresis = hysteresis_frames
        self._current_index: int | None = camera_indices[0] if camera_indices else None
        self._candidate_index: int | None = None
        self._candidate_count: int = 0

    @property
    def current_camera_index(self) -> int | None:
        return self._current_index

    def update(self, yaw_by_camera: dict[int, float | None]) -> int | None:
        """Given a dict of camera_index -> yaw (None = no face), return the
        camera index that should be active. The most front-facing camera
        (smallest absolute yaw) wins."""
        # Filter to cameras that detected a face
        candidates = {idx: yaw for idx, yaw in yaw_by_camera.items() if yaw is not None}

        if not candidates:
            self._candidate_count = 0
            self._candidate_index = None
            return self._current_index

        # Pick camera with smallest absolute yaw (most front-facing)
        target_index = min(candidates, key=lambda idx: abs(candidates[idx]))

        if target_index == self._current_index:
            self._candidate_count = 0
            self._candidate_index = None
            return self._current_index

        # Hysteresis: must consistently pick new camera for N frames
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
