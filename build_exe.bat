@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
set "PYTHONUTF8=1"
if /I not "%OS%"=="Windows_NT" (
    echo ERROR: Build the Windows EXE on Windows. PyInstaller is not a cross-compiler.
    exit /b 1
)
pushd "%~dp0"
if errorlevel 1 goto fail_nopop
if exist "dist\WikiVoiceDownloader" (
    echo ERROR: dist\WikiVoiceDownloader already exists.
    echo Preserve or rename it first. It may contain user downloads and will NOT be deleted.
    goto fail
)
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Run setup.bat first.
    goto fail
)
if exist ".build-venv\Scripts\python.exe" goto dependencies
if exist ".build-venv" (
    echo ERROR: Invalid .build-venv. Preserve or rename it manually.
    goto fail
)
".venv\Scripts\python.exe" -m venv ".build-venv"
if errorlevel 1 goto fail
:dependencies
".build-venv\Scripts\python.exe" -m pip install -r "requirements-build.txt"
if errorlevel 1 goto fail
".build-venv\Scripts\python.exe" -m pytest -q
if errorlevel 1 goto fail
".build-venv\Scripts\python.exe" -m PyInstaller --clean --distpath "dist" --workpath "build" "WikiVoiceDownloader.spec"
if errorlevel 1 goto fail
copy /Y "config.example.json" "dist\WikiVoiceDownloader\config.example.json" >nul
if errorlevel 1 goto fail
copy /Y "README.md" "dist\WikiVoiceDownloader\README.md" >nul
if errorlevel 1 goto fail
".build-venv\Scripts\python.exe" -m pip freeze > "dist\WikiVoiceDownloader\build-environment.txt"
"dist\WikiVoiceDownloader\WikiVoiceDownloader.exe" --help
if errorlevel 1 goto fail
echo.
echo Build finished: dist\WikiVoiceDownloader\WikiVoiceDownloader.exe
echo Distribute the WHOLE WikiVoiceDownloader folder, including _internal.
echo Run the manual Windows and live-site checklist before declaring release acceptance.
set "RESULT=0"
goto finish
:fail
echo.
echo Build failed. Review the error above. Existing user download folders were not cleared.
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
