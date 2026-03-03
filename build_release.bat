@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

set "APP_NAME=AutoClicker"
set "ENTRYPOINT=clicker.py"
set "VERSION_FILE=VERSION"
set "RELEASE_DIR=release"
set "DIST_DIR=dist"
set "BUILD_DIR=build"
set "VENV_DIR=.buildenv"

if exist "%VERSION_FILE%" (
    set /p APP_VERSION=<"%VERSION_FILE%"
) else (
    set "APP_VERSION=0.1.0"
)

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"') do set "STAMP=%%i"
set "PACKAGE_NAME=%APP_NAME%-%APP_VERSION%-win64-%STAMP%"
set "PACKAGE_PATH=%RELEASE_DIR%\%PACKAGE_NAME%"
set "ZIP_PATH=%RELEASE_DIR%\%PACKAGE_NAME%.zip"

echo ============================================
echo   %APP_NAME% - Build de Release
echo ============================================
echo Versao: %APP_VERSION%
echo Pacote: %PACKAGE_NAME%
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado no PATH.
    echo Instale Python 3.11+ e marque "Add Python to PATH".
    pause
    exit /b 1
)

echo [1/7] Atualizando pip...
if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo [1/8] Criando ambiente virtual de build...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 goto :error
)

set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"

echo [2/8] Atualizando pip...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto :error

echo [3/8] Instalando dependencias...
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [4/8] Limpando pastas de build...
if exist "%BUILD_DIR%" rmdir /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%"
if exist "%PACKAGE_PATH%" rmdir /s /q "%PACKAGE_PATH%"
if exist "%ZIP_PATH%" del /q "%ZIP_PATH%"

echo [5/8] Gerando executavel...
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean --onefile --windowed --name %APP_NAME% %ENTRYPOINT%
if errorlevel 1 goto :error

if not exist "%DIST_DIR%\%APP_NAME%.exe" (
    echo [ERRO] Nao encontrei %DIST_DIR%\%APP_NAME%.exe apos build.
    goto :error
)

echo [6/8] Montando pacote portavel...
mkdir "%PACKAGE_PATH%"
copy /y "%DIST_DIR%\%APP_NAME%.exe" "%PACKAGE_PATH%\%APP_NAME%.exe" >nul

(
echo @echo off
echo cd /d "%%~dp0"
echo start "" "%APP_NAME%.exe"
) > "%PACKAGE_PATH%\Executar %APP_NAME%.bat"

(
echo %APP_NAME% - Pacote portavel
echo.
echo 1^) Execute "Executar %APP_NAME%.bat"
echo 2^) Se o jogo estiver em modo Administrador, execute este app tambem em modo Administrador.
echo 3^) Fluxo recomendado: Validar alvo ^> Capturar e testar ^> Iniciar.
echo.
echo Atalhos:
echo F6 = Testar 1 clique
echo F7 = Iniciar/Parar
echo F8 = Capturar ponto
) > "%PACKAGE_PATH%\LEIA-ME.txt"

echo [7/8] Gerando checksum...
certutil -hashfile "%PACKAGE_PATH%\%APP_NAME%.exe" SHA256 > "%PACKAGE_PATH%\SHA256.txt"

echo [8/8] Compactando zip...
powershell -NoProfile -Command "Compress-Archive -Path '%PACKAGE_PATH%\*' -DestinationPath '%ZIP_PATH%' -Force"
if errorlevel 1 goto :error

echo.
echo [SUCESSO] Release gerada:
echo - Pasta: %CD%\%PACKAGE_PATH%
echo - Zip:   %CD%\%ZIP_PATH%
echo.
start "" explorer "%RELEASE_DIR%"
pause
exit /b 0

:error
echo.
echo [ERRO] Falha na geracao da release.
echo Verifique os logs acima.
pause
exit /b 1
