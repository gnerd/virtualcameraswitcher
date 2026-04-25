import logging
import threading
from dataclasses import dataclass

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CameraInfo:
    index: int
    name: str
    width: int
    height: int


def enumerate_cameras(max_index: int = 10) -> list[CameraInfo]:
    """Probe camera indices and return available cameras."""
    cameras = []
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            cameras.append(CameraInfo(index=i, name=f"Camera {i}", width=w, height=h))
            cap.release()
    return cameras


class CameraManager:
    """Manages multiple camera captures and provides the active feed."""

    def __init__(self, camera_indices: list[int], output_size: tuple[int, int] = (1280, 720)):
        self._captures: dict[int, cv2.VideoCapture] = {}
        self._output_size = output_size
        self._active_index: int | None = None
        self._lock = threading.Lock()

        for idx in camera_indices:
            cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
            if cap.isOpened():
                self._captures[idx] = cap
                logger.info("Opened camera %d", idx)
            else:
                logger.warning("Failed to open camera %d", idx)

        if self._captures:
            self._active_index = camera_indices[0]

    @property
    def active_index(self) -> int | None:
        return self._active_index

    @active_index.setter
    def active_index(self, idx: int):
        with self._lock:
            if idx in self._captures:
                self._active_index = idx

    @property
    def available_indices(self) -> list[int]:
        return list(self._captures.keys())

    def read_active(self) -> np.ndarray | None:
        """Read a frame from the active camera, resized to output dimensions."""
        with self._lock:
            if self._active_index is None:
                return None
            cap = self._captures.get(self._active_index)
            if cap is None:
                return None
            ret, frame = cap.read()
        if not ret or frame is None:
            return None
        return cv2.resize(frame, self._output_size)

    def read_camera(self, idx: int) -> np.ndarray | None:
        """Read a frame from a specific camera."""
        cap = self._captures.get(idx)
        if cap is None:
            return None
        ret, frame = cap.read()
        if not ret:
            return None
        return frame

    def close(self):
        for cap in self._captures.values():
            cap.release()
        self._captures.clear()
