@echo off
setlocal
set "ROOT=%~dp0.."

pushd "%ROOT%\frontend"
call npm.cmd run build
if errorlevel 1 goto :failed
popd

pushd "%ROOT%"

rem Pin the verified runtime once so later rebuilds do not layer on old app images.
docker image inspect mootcourt-lab-api:local-runtime >nul 2>&1
if errorlevel 1 docker tag mootcourt-lab-api:latest mootcourt-lab-api:local-runtime
if errorlevel 1 goto :failed

docker image inspect mootcourt-lab-web:local-runtime >nul 2>&1
if errorlevel 1 docker tag mootcourt-lab-web:latest mootcourt-lab-web:local-runtime
if errorlevel 1 goto :failed

rem Reuse local images only. Do not pull images or modify infrastructure volumes.
docker build --pull=false -f backend/Dockerfile.local -t mootcourt-lab-api:latest backend
if errorlevel 1 goto :failed
docker build --pull=false -f frontend/Dockerfile.local -t mootcourt-lab-web:latest frontend
if errorlevel 1 goto :failed
docker compose up -d --no-build api web
if errorlevel 1 goto :failed

popd
exit /b 0

:failed
set "RESULT=%ERRORLEVEL%"
popd 2>nul
exit /b %RESULT%
