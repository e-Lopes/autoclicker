@echo off
setlocal

cd /d "%~dp0"

echo ============================================
echo   AutoClicker DU - Setup e Build
echo ============================================
echo.

where python >nul 2>&1
if errorlevel 1 goto :no_python

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
echo [OK] Python detectado: %PY_VER%

echo.
echo [1/4] Atualizando pip...
python -m pip install --upgrade pip
if errorlevel 1 goto :step_error

echo.
echo [2/4] Instalando dependencias...
python -m pip install -r requirements.txt
if errorlevel 1 goto :step_error

echo.
echo [3/4] Limpando build anterior...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "AutoClicker.spec" del /q "AutoClicker.spec"

echo.
echo [4/4] Gerando executavel...
python -m PyInstaller --onefile --windowed --name AutoClicker clicker.py
if errorlevel 1 goto :step_error

if not exist "dist\AutoClicker.exe" goto :missing_exe

echo.
echo [SUCESSO] Build concluido.
echo Executavel: %CD%\dist\AutoClicker.exe
start "" explorer "dist"
pause
exit /b 0

:no_python
echo [ERRO] Python nao encontrado no PATH.
echo.
echo Como resolver:
echo 1. Instale Python 3.11+ em https://www.python.org/downloads/windows/
echo 2. Marque "Add Python to PATH" durante a instalacao.
echo 3. Reabra o terminal e rode este script novamente.
echo.
pause
exit /b 1

:step_error
echo.
echo [ERRO] Ocorreu falha em uma das etapas acima.
echo Corrija o problema e tente novamente.
echo.
pause
exit /b 1

:missing_exe
echo.
echo [ERRO] Build terminou sem gerar dist\AutoClicker.exe
echo Verifique mensagens do PyInstaller acima.
echo.
pause
exit /b 1
