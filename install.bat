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

:: Install Python dependencies
echo.
echo Installing Python dependencies...
pip install -r "%~dp0requirements.txt"

echo.
echo === Installation complete ===
echo.
echo Next steps:
echo   1. Run: python -m virtual_camera_switcher.main --setup
echo   2. Run: python -m virtual_camera_switcher.main --calibrate
echo   3. Run: python -m virtual_camera_switcher.main
echo.
pause
