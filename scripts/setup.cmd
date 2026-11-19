@echo off
rem FRAME demo setup for Windows. Needs Python 3.11+ and Node.js 18+. Options: --key-file PATH, --no-key,
rem --video (also install ffmpeg and ask for an OpenRouter key), --openrouter-key-file PATH. Keys later: scripts\keys.cmd
setlocal
cd /d "%~dp0.."
rem Opened by double-click (Explorer runs cmd /c this file): keep the window open at the end
set "PAUSE_AT_END="
set "LAUNCH=%cmdcmdline:"=%"
if /i "%LAUNCH%"=="%ComSpec% /c %~f0 " set "PAUSE_AT_END=1"
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.11+ is required: winget install Python.Python.3.12
  if defined PAUSE_AT_END pause
  exit /b 1
)
%PY% scripts\frame_demo.py setup %*
set "CODE=%errorlevel%"
if defined PAUSE_AT_END pause
exit /b %CODE%
