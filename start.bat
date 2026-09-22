@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0"
if errorlevel 1 goto fail_nopop
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Python environment missing. Run setup.bat first.
    set "RESULT=1"
    goto finish
)
".venv\Scripts\python.exe" -c "import requests, bs4" >nul 2>nul
if errorlevel 1 (
    echo ERROR: Dependencies missing. Run setup.bat to install the locked dependencies.
    set "RESULT=1"
    goto finish
)
".venv\Scripts\python.exe" "%~dp0main.py" %*
set "RESULT=%ERRORLEVEL%"
:finish
echo.
if not "%WVD_NO_PAUSE%"=="1" pause
popd
exit /b %RESULT%
:fail_nopop
echo ERROR: Cannot access the project directory.
if not "%WVD_NO_PAUSE%"=="1" pause
exit /b 1
