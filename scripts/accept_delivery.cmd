@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Python virtual environment not found: %PYTHON% 1>&2
  exit /b 2
)
pushd "%ROOT%\backend"
"%PYTHON%" -m mootcourt.cli.accept_delivery ^
  --database-url "mysql+aiomysql://mootcourt:change-me@127.0.0.1:3307/mootcourt" %*
set "RESULT=%ERRORLEVEL%"
popd
exit /b %RESULT%
