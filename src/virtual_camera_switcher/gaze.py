import logging
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


class GazeDetector:
    def __init__(self):
        self._face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._yaw: float | None = None

    @property
    def yaw(self) -> float | None:
        """Last computed yaw angle in degrees. Negative = looking left, positive = looking right."""
        return self._yaw

    def process_frame(self, frame_bgr: np.ndarray) -> float | None:
        """Process a BGR frame and return the head yaw angle in degrees, or None if no face found."""
        h, w = frame_bgr.shape[:2]
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        results = self._face_mesh.process(frame_rgb)

        if not results.multi_face_landmarks:
            self._yaw = None
            return None

        landmarks = results.multi_face_landmarks[0]
        image_points = np.array(
            [[landmarks.landmark[i].x * w, landmarks.landmark[i].y * h] for i in _LANDMARK_INDICES],
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
            self._yaw = None
            return None

        rotation_mat, _ = cv2.Rodrigues(rotation_vec)
        yaw = np.degrees(np.arctan2(rotation_mat[1, 0], rotation_mat[0, 0]))

        self._yaw = float(yaw)
        return self._yaw

    def close(self):
        self._face_mesh.close()
