@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "IMX_CHECK_PY=py -3"
py -3 --version >nul 2>&1
if not errorlevel 1 goto python_found
set "IMX_CHECK_PY=python"
python --version >nul 2>&1
if errorlevel 1 goto no_python
:python_found
if not exist ".check-venv\Scripts\python.exe" %IMX_CHECK_PY% -m venv .check-venv
if errorlevel 1 goto failed
".check-venv\Scripts\python.exe" -m pip --disable-pip-version-check install -r requirements-check.txt
if errorlevel 1 goto failed
".check-venv\Scripts\python.exe" -m backend.supplier_check
echo.
echo Report: IMKONEX_4TOCHKI_REPORT.json
pause
exit /b
:no_python
echo Python 3.12 or newer is required. See README.txt.
pause
exit /b 1
:failed
echo Setup failed. Please send a screenshot of this window.
pause
exit /b 1
