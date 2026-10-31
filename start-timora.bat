@echo off
setlocal
title Timora
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "STAMP=%~dp0.venv\requirements.installed"
set "LOG_DIR=%~dp0logs"
set "INSTALL_LOG=%~dp0logs\install.log"
set "PY_VERSION=3.12.10"
set "PY_INSTALLER=%TEMP%\timora-python-%PY_VERSION%.exe"
set "PY_URL=https://www.python.org/ftp/python/%PY_VERSION%/python-%PY_VERSION%-amd64.exe"
set "CHECK=import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1

echo.
echo   TIMORA - Smart Employee Time Tracking
echo   -------------------------------------
echo.

if not exist "%~dp0run.py" goto err_incomplete
if not exist "%~dp0requirements.txt" goto err_incomplete

rem ---- Existing environment: skip straight to the dependency check ----
if not exist "%VENV_PY%" goto find_python
"%VENV_PY%" -c "%CHECK%" >nul 2>&1
if not errorlevel 1 goto deps
echo   The saved environment no longer works. Rebuilding it...
rmdir /s /q "%~dp0.venv" >nul 2>&1
if exist "%~dp0.venv" goto err_venv_locked

rem ---- 1. Python ----
:find_python
echo   [1/3] Checking for Python...
set "PY_CMD="
call :try_python py -3
if defined PY_CMD goto make_venv
call :try_python python
if defined PY_CMD goto make_venv
call :try_python python3
if defined PY_CMD goto make_venv
call :try_known_locations
if defined PY_CMD goto make_venv

if defined TIMORA_NO_AUTO_INSTALL goto err_no_python
echo         Python was not found. Installing Python %PY_VERSION%. This can take a few minutes...
call :install_with_winget
call :try_known_locations
if defined PY_CMD goto make_venv
call :install_from_python_org
call :try_known_locations
if defined PY_CMD goto make_venv
goto err_no_python

rem ---- 2. Virtual environment ----
:make_venv
echo   [2/3] Creating the Timora environment. This only happens once...
%PY_CMD% -m venv "%~dp0.venv" > "%INSTALL_LOG%" 2>&1
if errorlevel 1 goto err_venv
if not exist "%VENV_PY%" goto err_venv

rem ---- 3. Dependencies, installed only when something changed ----
:deps
if not exist "%STAMP%" goto install_deps
fc /b "%STAMP%" "%~dp0requirements.txt" >nul 2>&1
if errorlevel 1 goto install_deps
"%VENV_PY%" -c "import flask, waitress, tzdata" >nul 2>&1
if errorlevel 1 goto install_deps
goto start

:install_deps
echo   [3/3] Installing required components. This needs internet the first time...
"%VENV_PY%" -m pip install --disable-pip-version-check -r "%~dp0requirements.txt" > "%INSTALL_LOG%" 2>&1
if errorlevel 1 goto err_deps
copy /y "%~dp0requirements.txt" "%STAMP%" >nul 2>&1

:start
echo   Starting Timora...
"%VENV_PY%" "%~dp0run.py" --open
if errorlevel 1 goto stopped_with_error
goto :eof

:stopped_with_error
echo.
pause
exit /b 1

rem ---- Helpers ----
:try_python
%* -c "%CHECK%" >nul 2>&1
if not errorlevel 1 set "PY_CMD=%*"
exit /b 0

:try_path
if defined PY_CMD exit /b 0
if not exist "%~1" exit /b 0
"%~1" -c "%CHECK%" >nul 2>&1
if not errorlevel 1 set PY_CMD="%~1"
exit /b 0

:try_known_locations
for %%V in (314 313 312 311 310) do call :try_path "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
for %%V in (314 313 312 311 310) do call :try_path "%ProgramFiles%\Python%%V\python.exe"
exit /b 0

:install_with_winget
where winget >nul 2>&1
if errorlevel 1 exit /b 0
winget install --exact --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements >> "%INSTALL_LOG%" 2>&1
exit /b 0

:install_from_python_org
echo         Downloading Python from python.org...
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; Invoke-WebRequest -UseBasicParsing -Uri '%PY_URL%' -OutFile '%PY_INSTALLER%'" >> "%INSTALL_LOG%" 2>&1
if errorlevel 1 exit /b 0
echo         Installing Python...
"%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=0 >> "%INSTALL_LOG%" 2>&1
del /q "%PY_INSTALLER%" >nul 2>&1
exit /b 0

rem ---- Clear error messages ----
:err_no_python
call :banner
echo   Problem:
echo     Python 3.10 or newer is needed, and it could not be installed
echo     automatically.
echo.
echo   How to fix it:
echo     1. Check that this computer is connected to the internet.
echo     2. Go to https://www.python.org/downloads/ and download Python.
echo     3. Run the installer and tick "Add python.exe to PATH".
echo     4. Double-click start-timora.bat again.
goto fail

:err_venv
call :banner
echo   Problem:
echo     The Timora environment could not be created.
echo.
echo   How to fix it:
echo     1. Check that the disk has at least 200 MB free.
echo     2. Move the Timora folder to your Desktop or Documents, in case
echo        Windows is blocking writes to the current location.
echo     3. If antivirus software is running, allow this folder.
echo     4. Double-click start-timora.bat again.
echo.
echo   Details are saved in: %INSTALL_LOG%
goto fail

:err_venv_locked
call :banner
echo   Problem:
echo     The old Timora environment is in use and could not be replaced.
echo.
echo   How to fix it:
echo     1. Close any other Timora window that is open.
echo     2. Delete the .venv folder inside the Timora folder.
echo     3. Double-click start-timora.bat again.
goto fail

:err_deps
call :banner
echo   Problem:
echo     The required components could not be downloaded and installed.
echo.
echo   How to fix it:
echo     1. Check that this computer is connected to the internet.
echo     2. If your company uses a proxy or firewall, ask your IT team to
echo        allow pypi.org and files.pythonhosted.org.
echo     3. Double-click start-timora.bat again.
echo.
echo   Details are saved in: %INSTALL_LOG%
goto fail

:err_incomplete
call :banner
echo   Problem:
echo     Some Timora files are missing from this folder.
echo.
echo   How to fix it:
echo     1. If you opened this from inside a ZIP file, extract the whole
echo        ZIP first: right-click it and choose "Extract All".
echo     2. Run start-timora.bat from the extracted folder.
echo     3. If files are still missing, download Timora again.
goto fail

:banner
echo.
echo ==================================================================
echo   TIMORA COULD NOT START
echo ==================================================================
echo.
exit /b 0

:fail
echo.
echo ==================================================================
echo.
pause
exit /b 1
