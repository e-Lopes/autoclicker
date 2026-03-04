@echo off
setlocal EnableExtensions

cd /d "%~dp0"

set "APP_NAME=AutoClicker"
set "EXE_PATH=%~dp0%APP_NAME%.exe"

if not exist "%EXE_PATH%" (
    echo [ERRO] Nao encontrei "%EXE_PATH%".
    echo Coloque este arquivo na mesma pasta do AutoClicker.exe e execute novamente.
    pause
    exit /b 1
)

for /f "usebackq delims=" %%D in (`powershell -NoProfile -Command "[Environment]::GetFolderPath('Desktop')"`) do set "DESKTOP_DIR=%%D"
if not defined DESKTOP_DIR (
    echo [ERRO] Nao foi possivel localizar a Area de Trabalho.
    pause
    exit /b 1
)

set "SHORTCUT_PATH=%DESKTOP_DIR%\%APP_NAME%.lnk"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; " ^
  "$sc = $ws.CreateShortcut('%SHORTCUT_PATH%'); " ^
  "$sc.TargetPath = '%EXE_PATH%'; " ^
  "$sc.WorkingDirectory = '%~dp0'; " ^
  "$sc.IconLocation = '%EXE_PATH%,0'; " ^
  "$sc.Save()"

if errorlevel 1 (
    echo [ERRO] Falha ao criar atalho.
    pause
    exit /b 1
)

echo [SUCESSO] Atalho criado em:
echo %SHORTCUT_PATH%
pause
exit /b 0

