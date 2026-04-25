import logging
import time

import cv2
import numpy as np

from .config import AppConfig, CameraCalibration
from .cameras import CameraManager
from .gaze import GazeDetector

logger = logging.getLogger(__name__)


def run_calibration(config: AppConfig, camera_manager: CameraManager) -> list[CameraCalibration]:
    """Interactive calibration: user looks at each camera for a few seconds to record yaw angles."""
    detector = GazeDetector()
    gaze_cap = cv2.VideoCapture(config.gaze_camera_index, cv2.CAP_DSHOW)

    if not gaze_cap.isOpened():
        logger.error("Cannot open gaze camera %d for calibration", config.gaze_camera_index)
        return []

    calibrations = []
    window_name = "Virtual Camera Switcher - Calibration"
    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)

    for i, cam_idx in enumerate(config.cameras):
        yaw_samples: list[float] = []
        label = f"Camera {cam_idx}"
        prompt = f"Look at {label} (camera index {cam_idx}) and press SPACE when ready..."

        # Wait for user to press space
        while True:
            ret, frame = gaze_cap.read()
            if not ret:
                continue
            display = frame.copy()
            cv2.putText(display, prompt, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(
                display,
                f"Calibrating camera {i + 1}/{len(config.cameras)}",
                (20, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1,
            )
            cv2.imshow(window_name, display)
            key = cv2.waitKey(1) & 0xFF
            if key == ord(" "):
                break
            if key == 27:  # ESC
                cv2.destroyWindow(window_name)
                gaze_cap.release()
                detector.close()
                return []

        # Collect samples for 2 seconds
        start = time.time()
        while time.time() - start < 2.0:
            ret, frame = gaze_cap.read()
            if not ret:
                continue
            yaw = detector.process_frame(frame)
            if yaw is not None:
                yaw_samples.append(yaw)

            display = frame.copy()
            status = f"Recording... ({len(yaw_samples)} samples)"
            cv2.putText(display, status, (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
            cv2.imshow(window_name, display)
            cv2.waitKey(1)

        if yaw_samples:
            mean_yaw = float(np.median(yaw_samples))
            calibrations.append(CameraCalibration(camera_index=cam_idx, label=label, yaw_center=mean_yaw))
            logger.info("Camera %d calibrated: yaw=%.1f° (%d samples)", cam_idx, mean_yaw, len(yaw_samples))
        else:
            logger.warning("No face detected during calibration for camera %d", cam_idx)

    cv2.destroyWindow(window_name)
    gaze_cap.release()
    detector.close()

    # Sort calibrations by yaw (left to right)
    calibrations.sort(key=lambda c: c.yaw_center)
    return calibrations
