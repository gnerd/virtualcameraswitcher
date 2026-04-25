import logging
from pathlib import Path

import numpy as np
import cv2
import mediapipe as mp

logger = logging.getLogger(__name__)

# 3D model points for head pose estimation (generic face model, in mm)
_MODEL_POINTS = np.array([
    [0.0, 0.0, 0.0],          # Nose tip
    [0.0, -63.6, -12.5],      # Chin
    [-43.3, 32.7, -26.0],     # Left eye left corner
    [43.3, 32.7, -26.0],      # Right eye right corner
    [-28.9, -28.9, -24.1],    # Left mouth corner
    [28.9, -28.9, -24.1],     # Right mouth corner
], dtype=np.float64)

# MediaPipe landmark indices for the 6 model points above
_LANDMARK_INDICES = [1, 152, 33, 263, 61, 291]

_MODEL_PATH = Path(__file__).parent / "face_landmarker.task"


class GazeDetector:
    def __init__(self, max_width: int | None = 480):
        """`max_width`: if set, frames wider than this are downscaled before
        running MediaPipe. Yaw is geometric and is unaffected by downscaling,
        but detection becomes ~10x faster at 480px wide."""
        self._max_width = max_width
        base_options = mp.tasks.BaseOptions(model_asset_path=str(_MODEL_PATH))
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=base_options,
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.5,
        )
        self._landmarker = mp.tasks.vision.FaceLandmarker.create_from_options(options)

    def detect_yaw(self, frame_bgr: np.ndarray) -> float | None:
        """Return the head yaw angle in degrees, or None if no face found.
        Closer to 0 means the person is facing the camera straight-on."""
        if self._max_width and frame_bgr.shape[1] > self._max_width:
            scale = self._max_width / frame_bgr.shape[1]
            new_size = (self._max_width, int(round(frame_bgr.shape[0] * scale)))
            frame_bgr = cv2.resize(frame_bgr, new_size, interpolation=cv2.INTER_AREA)
        h, w = frame_bgr.shape[:2]
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)
        results = self._landmarker.detect(mp_image)

        if not results.face_landmarks:
            return None

        landmarks = results.face_landmarks[0]
        image_points = np.array(
            [[landmarks[i].x * w, landmarks[i].y * h] for i in _LANDMARK_INDICES],
            dtype=np.float64,
        )

        focal_length = w
        center = (w / 2, h / 2)
        camera_matrix = np.array(
            [[focal_length, 0, center[0]],
             [0, focal_length, center[1]],
             [0, 0, 1]],
            dtype=np.float64,
        )
        dist_coeffs = np.zeros((4, 1))

        success, rotation_vec, _ = cv2.solvePnP(
            _MODEL_POINTS, image_points, camera_matrix, dist_coeffs,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not success:
            return None

        rotation_mat, _ = cv2.Rodrigues(rotation_vec)
        # Decompose to Tait-Bryan (yaw-pitch-roll). Using cv2.RQDecomp3x3
        # gives the three Euler angles in degrees directly. The order of the
        # returned tuple is (pitch_x, yaw_y, roll_z).
        euler, *_ = cv2.RQDecomp3x3(rotation_mat)
        yaw = float(euler[1])
        # OpenCV/PnP returns yaw with a 180-degree ambiguity: looking at the
        # camera often comes back as ~±180 instead of 0. Wrap into [-90, 90]
        # so "front-facing" is always near zero.
        if yaw > 90.0:
            yaw -= 180.0
        elif yaw < -90.0:
            yaw += 180.0
        return yaw

    def close(self):
        self._landmarker.close()
