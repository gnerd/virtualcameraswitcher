import argparse
import logging
import sys
import threading
import time

import cv2

from .calibration import run_calibration
from .cameras import CameraManager, enumerate_cameras
from .config import AppConfig
from .gaze import GazeDetector
from .switcher import CameraSwitcher
from .tray import TrayApp
from .virtual_cam import VirtualCameraOutput

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class App:
    def __init__(self, config: AppConfig):
        self.config = config
        self.running = False
        self._pipeline_thread: threading.Thread | None = None
        self._camera_manager: CameraManager | None = None
        self._virtual_cam: VirtualCameraOutput | None = None
        self._gaze_detector: GazeDetector | None = None
        self._switcher: CameraSwitcher | None = None

    def start_pipeline(self):
        """Start the camera switching pipeline in a background thread."""
        if not self.config.cameras:
            logger.error("No cameras configured. Run with --setup first.")
            return

        self._camera_manager = CameraManager(
            self.config.cameras,
            (self.config.output_width, self.config.output_height),
        )
        self._virtual_cam = VirtualCameraOutput(
            self.config.output_width, self.config.output_height, self.config.output_fps,
        )
        self._gaze_detector = GazeDetector()
        self._switcher = CameraSwitcher(
            self.config.calibrations, self.config.hysteresis_frames,
        )

        # Set initial active camera
        if self.config.calibrations:
            self._camera_manager.active_index = self.config.calibrations[0].camera_index
            self._switcher._current_index = self.config.calibrations[0].camera_index

        self._virtual_cam.start()
        self.running = True
        self._pipeline_thread = threading.Thread(target=self._run_pipeline, daemon=True)
        self._pipeline_thread.start()
        logger.info("Pipeline started")

    def _run_pipeline(self):
        gaze_cap = cv2.VideoCapture(self.config.gaze_camera_index, cv2.CAP_DSHOW)
        if not gaze_cap.isOpened():
            logger.error("Cannot open gaze camera %d", self.config.gaze_camera_index)
            return

        try:
            while self.running:
                # Read gaze camera for head pose detection
                ret, gaze_frame = gaze_cap.read()
                if ret and gaze_frame is not None:
                    yaw = self._gaze_detector.process_frame(gaze_frame)
                    new_cam = self._switcher.update(yaw)
                    if new_cam is not None:
                        self._camera_manager.active_index = new_cam

                # Read active camera and send to virtual camera
                frame = self._camera_manager.read_active()
                if frame is not None:
                    self._virtual_cam.send_frame(frame)
        finally:
            gaze_cap.release()

    def stop_pipeline(self):
        self.running = False
        if self._pipeline_thread:
            self._pipeline_thread.join(timeout=5)
        if self._virtual_cam:
            self._virtual_cam.close()
        if self._camera_manager:
            self._camera_manager.close()
        if self._gaze_detector:
            self._gaze_detector.close()
        logger.info("Pipeline stopped")

    def calibrate(self):
        was_running = self.running
        if was_running:
            self.stop_pipeline()

        cam_mgr = CameraManager(
            self.config.cameras,
            (self.config.output_width, self.config.output_height),
        )
        cals = run_calibration(self.config, cam_mgr)
        cam_mgr.close()

        if cals:
            self.config.calibrations = cals
            self.config.save()
            logger.info("Calibration complete: %d cameras calibrated", len(cals))
        else:
            logger.warning("Calibration cancelled or failed")

        if was_running:
            self.start_pipeline()

    def toggle(self):
        if self.running:
            self.stop_pipeline()
        else:
            self.start_pipeline()


def interactive_setup() -> AppConfig:
    """Interactive first-time setup."""
    print("\n=== Virtual Camera Switcher - Setup ===\n")

    print("Scanning for cameras...")
    cameras = enumerate_cameras()
    if not cameras:
        print("No cameras found!")
        sys.exit(1)

    print(f"\nFound {len(cameras)} camera(s):")
    for cam in cameras:
        print(f"  [{cam.index}] {cam.name} ({cam.width}x{cam.height})")

    print("\nEnter camera indices to use (comma-separated), or 'all':")
    choice = input("> ").strip()
    if choice.lower() == "all":
        selected = [c.index for c in cameras]
    else:
        selected = [int(x.strip()) for x in choice.split(",")]

    print("\nWhich camera should be used for gaze detection?")
    print("(This camera reads your face to determine where you're looking)")
    print(f"Available: {selected}")
    gaze_idx = int(input("> ").strip())

    config = AppConfig(cameras=selected, gaze_camera_index=gaze_idx)
    config.save()
    print(f"\nConfig saved. You have {len(selected)} cameras configured.")
    print("Run calibration next to map your head direction to each camera.\n")
    return config


def main():
    parser = argparse.ArgumentParser(description="Virtual Camera Switcher")
    parser.add_argument("--setup", action="store_true", help="Run interactive setup")
    parser.add_argument("--calibrate", action="store_true", help="Run calibration")
    parser.add_argument("--no-tray", action="store_true", help="Run without system tray (console only)")
    args = parser.parse_args()

    if args.setup:
        config = interactive_setup()
    else:
        config = AppConfig.load()

    if not config.cameras:
        print("No cameras configured. Run with --setup first.")
        sys.exit(1)

    app = App(config)

    if args.calibrate:
        app.calibrate()
        if not config.calibrations:
            print("Calibration required before running. Use --calibrate.")
            sys.exit(1)

    if not config.calibrations:
        print("No calibration data. Run with --calibrate first.")
        sys.exit(1)

    if args.no_tray:
        app.start_pipeline()
        print("Pipeline running. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            app.stop_pipeline()
    else:
        app.start_pipeline()
        tray = TrayApp(
            config,
            on_calibrate=app.calibrate,
            on_toggle=app.toggle,
            on_quit=lambda: (app.stop_pipeline(), tray.stop()),
        )
        tray.run()


if __name__ == "__main__":
    main()
