---
title: Virtual Camera Switcher
description: >-
  A Windows 11 application that uses head-pose gaze detection to
  automatically switch between physical cameras, outputting the active
  feed through a virtual camera device.
---

## Overview

Virtual Camera Switcher detects which physical camera you are looking at
and automatically switches the virtual camera feed to that camera in real
time. Video-calling apps such as Zoom, Teams, and Discord see a single
virtual camera that seamlessly changes its source as you turn your head.

## How it works

1. **MediaPipe Face Mesh** tracks 478 facial landmarks on each frame from
   a designated gaze-detection camera.
2. **OpenCV solvePnP** converts the landmarks into a head-pose yaw angle.
3. A **hysteresis-based switcher** maps the yaw angle to the nearest
   calibrated camera and holds for several frames before committing a
   switch, preventing flickering.
4. The active camera's frames are pushed to a **pyvirtualcam** virtual
   camera device that appears as a regular webcam in any application.

## Prerequisites

- Windows 10 or 11
- Python 3.10+
- A virtual camera backend supported by pyvirtualcam (OBS Virtual Camera
  or Unity Capture)

## Installation

```bash
pip install -e .
```

To register the Unity Capture virtual camera filter (requires admin):

```cmd
install.bat
```

## Usage

### First-time setup

Scan for cameras and choose which ones to include:

```bash
vcs --setup
```

### Calibration

Look at each camera when prompted so the app learns which head direction
maps to which camera:

```bash
vcs --calibrate
```

### Run

Start the virtual camera switcher as a system-tray application:

```bash
vcs
```

Use `--no-tray` to run in console mode instead.

## Configuration

Settings are stored in `~/.virtual-camera-switcher/config.json`:

| Setting                | Default | Description                                     |
|------------------------|---------|-------------------------------------------------|
| `cameras`              | `[]`    | List of camera indices to use                   |
| `gaze_camera_index`    | `0`     | Camera used for gaze detection                  |
| `output_width`         | `1280`  | Virtual camera output width                     |
| `output_height`        | `720`   | Virtual camera output height                    |
| `output_fps`           | `30`    | Virtual camera frame rate                       |
| `hysteresis_frames`    | `10`    | Frames before committing a camera switch        |
| `switch_threshold_degrees` | `5.0` | Unused reserve for future threshold tuning    |

## Architecture

```text
┌──────────────┐   frames   ┌───────────────┐
│ Gaze Camera  │──────────▶│ GazeDetector   │──▶ yaw angle
└──────────────┘            └───────────────┘       │
                                                     ▼
┌──────────────┐            ┌───────────────┐  ┌─────────────┐
│ Camera 0     │──────────▶│               │  │ Switcher    │
├──────────────┤            │ CameraManager │◀─┤ (hysteresis)│
│ Camera 1     │──────────▶│               │  └─────────────┘
└──────────────┘            └───────┬───────┘
                                    │ active frame
                                    ▼
                            ┌───────────────┐
                            │ VirtualCamera │──▶ Zoom / Teams / etc.
                            └───────────────┘
```

## Project structure

```text
src/virtual_camera_switcher/
  __init__.py
  main.py           # Entry point and App orchestration
  config.py         # Configuration dataclasses and persistence
  gaze.py           # MediaPipe + solvePnP head-pose detection
  cameras.py        # Multi-camera capture management
  switcher.py       # Hysteresis-based camera switching logic
  calibration.py    # Interactive calibration workflow
  virtual_cam.py    # pyvirtualcam output wrapper
  tray.py           # System tray UI (pystray)
```
