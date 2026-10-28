@echo off
REM Windows launcher (experimental). One-time setup:  py -m pip install soundcard numpy pystray pywebview pillow
set DIR=%~dp0
if exist "%USERPROFILE%\.hermes\hermes-agent\venv\Scripts\pythonw.exe" (
  start "" "%USERPROFILE%\.hermes\hermes-agent\venv\Scripts\pythonw.exe" "%DIR%livescribe_gui.py" %*
) else (
  start "" pyw "%DIR%livescribe_gui.py" %*
)
