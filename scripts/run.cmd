@echo off
rem FRAME demo run for Windows. Options: --offline --lan --port N --reset --no-browser
rem --video: real video generation with OpenRouter (paid per scene, needs setup --video and an OpenRouter key)
setlocal
cd /d "%~dp0.."
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.11+ is required: winget install Python.Python.3.12
  exit /b 1
)
%PY% scripts\frame_demo.py run %*
exit /b %errorlevel%
