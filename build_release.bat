@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "APP_NAME=AutoClicker"
set "ENTRYPOINT=clicker.py"
set "VENV_DIR=.buildenv"
set "BUILD_DIR=build"
set "DIST_DIR=dist"
set "RELEASE_DIR=release"
set "EXE_OUT=%RELEASE_DIR%\%APP_NAME%.exe"
set "SHORTCUT_SCRIPT_IN=%CD%\CriarAtalhoDesktop.bat"
set "SHORTCUT_SCRIPT_OUT=%RELEASE_DIR%\CriarAtalhoDesktop.bat"
set "ZIP_OUT=%RELEASE_DIR%\%APP_NAME%-win64.zip"

echo ============================================
echo   %APP_NAME% - Build Release
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH.
    pause
    exit /b 1
)

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [1/6] Criando ambiente virtual...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 goto :error
)

set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

echo [2/6] Instalando dependencias...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto :error
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [3/6] Limpando pastas temporarias...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if not exist "%RELEASE_DIR%" mkdir "%RELEASE_DIR%"
for /d %%d in ("%RELEASE_DIR%\*") do rmdir /s /q "%%d"
del /q "%RELEASE_DIR%\*" >nul 2>&1

echo [4/6] Gerando executavel...
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean --onefile --windowed --name %APP_NAME% %ENTRYPOINT%
if errorlevel 1 goto :error
if not exist "%DIST_DIR%\%APP_NAME%.exe" goto :error

echo [5/6] Copiando executavel final...
copy /y "%DIST_DIR%\%APP_NAME%.exe" "%EXE_OUT%" >nul

if exist "%SHORTCUT_SCRIPT_IN%" (
    copy /y "%SHORTCUT_SCRIPT_IN%" "%SHORTCUT_SCRIPT_OUT%" >nul
)

echo [6/6] Gerando zip...
if exist "%SHORTCUT_SCRIPT_OUT%" (
    powershell -NoProfile -Command "Compress-Archive -Path '%EXE_OUT%','%SHORTCUT_SCRIPT_OUT%' -DestinationPath '%ZIP_OUT%' -Force"
) else (
    powershell -NoProfile -Command "Compress-Archive -Path '%EXE_OUT%' -DestinationPath '%ZIP_OUT%' -Force"
)
if errorlevel 1 goto :error

echo.
echo [SUCESSO] Release pronta:
echo - %CD%\%EXE_OUT%
if exist "%SHORTCUT_SCRIPT_OUT%" echo - %CD%\%SHORTCUT_SCRIPT_OUT%
echo - %CD%\%ZIP_OUT%
start "" explorer "%RELEASE_DIR%"
pause
exit /b 0

:error
echo.
echo [ERRO] Falha na geracao da release.
pause
exit /b 1
