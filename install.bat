@echo off
echo === Virtual Camera Switcher - Installer ===
echo.

:: Check for admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo This installer requires administrator privileges.
    echo Right-click and select "Run as administrator".
    pause
    exit /b 1
)

:: Register Unity Capture virtual camera filter
echo Registering virtual camera filter...
if exist "%~dp0filters\UnityCaptureFilter64.dll" (
    regsvr32 /s "%~dp0filters\UnityCaptureFilter64.dll"
    echo   64-bit filter registered.
) else (
    echo   WARNING: UnityCaptureFilter64.dll not found in filters\ directory.
    echo   You need to download it from https://github.com/schellingb/UnityCapture
    echo   and place it in the filters\ subdirectory.
)

:: Install Python package (editable) with dependencies
echo.
echo Installing Python package...
pip install -e "%~dp0."

:: Download face landmarker model if missing
set MODEL_PATH=%~dp0src\virtual_camera_switcher\face_landmarker.task
if not exist "%MODEL_PATH%" (
    echo.
    echo Downloading MediaPipe face landmarker model...
    python -c "import urllib.request; urllib.request.urlretrieve('https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task', r'%MODEL_PATH%')"
    echo   Model downloaded.
) else (
    echo   Face landmarker model already present.
)

echo.
echo === Installation complete ===
echo.
echo Next steps:
echo   1. Run: vcs --setup
echo   2. Run: vcs
echo.
pause
