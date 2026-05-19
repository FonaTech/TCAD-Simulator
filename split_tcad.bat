@echo off
setlocal

set "ROOT=%~dp0"
set "TOOL=%ROOT%tools\split_tcad.py"
set "SRC=%~1"
set "OUT=%~2"

if "%SRC%"=="" set "SRC=tcad_simulator.py"
if "%OUT%"=="" set "OUT=tcad_simulator_split"

if defined PYTHON (
    "%PYTHON%" "%TOOL%" --src "%SRC%" --out "%OUT%" --clean --dedupe conservative
    if errorlevel 1 exit /b %ERRORLEVEL%
    "%PYTHON%" "%TOOL%" --out "%OUT%" --verify
    if errorlevel 1 exit /b %ERRORLEVEL%
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3 "%TOOL%" --src "%SRC%" --out "%OUT%" --clean --dedupe conservative
        if errorlevel 1 exit /b %ERRORLEVEL%
        py -3 "%TOOL%" --out "%OUT%" --verify
        if errorlevel 1 exit /b %ERRORLEVEL%
    ) else (
        python "%TOOL%" --src "%SRC%" --out "%OUT%" --clean --dedupe conservative
        if errorlevel 1 exit /b %ERRORLEVEL%
        python "%TOOL%" --out "%OUT%" --verify
        if errorlevel 1 exit /b %ERRORLEVEL%
    )
)

echo Split complete: %OUT%
echo Docs: %OUT%/docs
echo Docs HTML: %OUT%/docs_html/index.html
echo Report: %OUT%/SPLIT_REPORT.json
