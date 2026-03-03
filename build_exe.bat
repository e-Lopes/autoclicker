@echo off
setlocal

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo [ERRO] Python nao encontrado no PATH.
  echo Instale o Python 3.11+ e marque "Add Python to PATH".
  echo.
  pause
  exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 goto :build_error

python -m pip install -r requirements.txt
if errorlevel 1 goto :build_error

python -m PyInstaller --onefile --windowed --name AutoClicker clicker.py
if errorlevel 1 goto :build_error

echo.
echo Build finalizado com sucesso. Verifique dist\AutoClicker.exe
if exist "dist" start "" explorer "dist"
pause
exit /b 0

:build_error
echo.
echo [ERRO] Falha durante o build. Veja as mensagens acima.
pause
exit /b 1
