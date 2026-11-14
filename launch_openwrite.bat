@echo off
title OpenWrite Desktop Launcher
cd /d "%~dp0"

:: Close previous instance using saved PID
if exist "data\desktop_app.pid" (
    set /p OLD_PID=<"data\desktop_app.pid"
    if defined OLD_PID (
        taskkill /F /PID %OLD_PID% >nul 2>&1
    )
)

:: Launch fresh OpenWrite Native Executable
if exist "OpenWrite.exe" (
    start "" "OpenWrite.exe"
) else (
    start "" "C:\Program Files\Python310\pythonw.exe" "%~dp0desktop_app.py"
)
