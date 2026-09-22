@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
pushd "%~dp0"
if errorlevel 1 goto fail_nopop
if exist ".venv\Scripts\python.exe" goto install
if exist ".venv" (
    echo ERROR: .venv exists but has no usable Windows Python.
    echo Preserve or rename this directory manually, then run setup.bat again.
    goto fail
)
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
if errorlevel 1 goto try_python
py -3 -m venv ".venv"
if errorlevel 1 goto fail
goto install
:try_python
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
if errorlevel 1 (
    echo ERROR: Install Python 3.11 or newer with the Python launcher or PATH enabled.
    goto fail
)
python -m venv ".venv"
if errorlevel 1 goto fail
:install
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
    echo ERROR: The existing environment uses an unsupported Python version.
    goto fail
)
".venv\Scripts\python.exe" -m pip install -r "requirements.txt"
if errorlevel 1 goto fail
echo.
echo Setup completed. Run start.bat.
echo Existing config.json was not modified. Defaults work without a config file.
set "RESULT=0"
goto finish
:fail
echo.
echo Setup failed. Check the error above. No system Python was modified.
set "RESULT=1"
goto finish
:fail_nopop
echo ERROR: Cannot access the project directory.
if not "%WVD_NO_PAUSE%"=="1" pause
exit /b 1
:finish
if not "%WVD_NO_PAUSE%"=="1" pause
popd
exit /b %RESULT%
