@echo off
setlocal

echo === Checking Docker ===
docker info >nul 2>&1
if errorlevel 1 (
    echo ERROR: Docker is not running. Start Docker Desktop and try again.
    exit /b 1
)

set IMAGE=pandoc/extra:latest

echo === Pulling pandoc Docker image ===
docker pull %IMAGE%

echo.
echo === Building PDF inside container ===
docker run --rm --entrypoint sh -v "%cd%:/workspace" -w /workspace %IMAGE% /workspace/build-pdf.sh

if errorlevel 1 (
    echo.
    echo ERROR: PDF build failed. Check the output above.
    exit /b 1
)

echo.
echo === Done! ===
echo Output: %cd%\ChiaLisp-Learning-Guide.pdf

endlocal
