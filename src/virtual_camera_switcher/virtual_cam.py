import logging
import numpy as np
import cv2
import pyvirtualcam

logger = logging.getLogger(__name__)


class VirtualCameraOutput:
    """Wraps pyvirtualcam to send frames to a virtual camera device."""

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 30, device: str | None = None):
        self._width = width
        self._height = height
        self._fps = fps
        self._device = device
        self._cam: pyvirtualcam.Camera | None = None

    def start(self):
        try:
            self._cam = pyvirtualcam.Camera(
                width=self._width,
                height=self._height,
                fps=self._fps,
                device=self._device,
                fmt=pyvirtualcam.PixelFormat.BGR,
            )
            logger.info(
                "Virtual camera started: %s (%dx%d @ %d fps)",
                self._cam.device, self._width, self._height, self._fps,
            )
        except Exception:
            logger.exception("Failed to start virtual camera")
            raise

    def send_frame(self, frame_bgr: np.ndarray):
        if self._cam is None:
            return
        if frame_bgr.shape[1] != self._width or frame_bgr.shape[0] != self._height:
            frame_bgr = cv2.resize(frame_bgr, (self._width, self._height))
        self._cam.send(frame_bgr)
        self._cam.sleep_until_next_frame()

    def close(self):
        if self._cam is not None:
            self._cam.close()
            self._cam = None
