@echo off
setlocal

set "ROOT=%~dp0"
set "SCRIPT=%ROOT%tcad_simulator.py"

if defined PYTHON (
    "%PYTHON%" "%SCRIPT%" %*
    exit /b %ERRORLEVEL%
)

where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%SCRIPT%" %*
    exit /b %ERRORLEVEL%
)

python "%SCRIPT%" %*
exit /b %ERRORLEVEL%
