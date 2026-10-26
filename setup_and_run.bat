@echo off
setlocal enabledelayedexpansion
title Driver Reinstaller - Setup

set "PY_VER=3.13.3"
set "PY_URL_64=https://www.python.org/ftp/python/3.13.3/python-3.13.3-amd64.exe"
set "PY_URL_32=https://www.python.org/ftp/python/3.13.3/python-3.13.3.exe"
set "PACKAGES=requests wmi pywin32"
set "APP=%~dp0driver_reinstaller.py"
set "DL_FILE=%TEMP%\python_setup_%RANDOM%.exe"
set "PYTHON_EXE="

cls
echo.
echo  +----------------------------------------------------------+
echo  ^|       Driver Reinstaller -- Setup and Launch            ^|
echo  +----------------------------------------------------------+
echo.

:: Architecture
set "ARCH=x64"
if "%PROCESSOR_ARCHITECTURE%"=="x86" (
    if not defined PROCESSOR_ARCHITEW6432 set "ARCH=x86"
)
echo  [i] Architecture: %ARCH%

:: ============================================================
::  STEP 1 - Find Python in PATH
:: ============================================================
echo.
echo  [1/4] Looking for Python 3.10+...

for %%C in (python py python3) do (
    if not defined PYTHON_EXE (
        %%C --version >nul 2>&1
        if !errorlevel! == 0 (
            for /f "tokens=2 delims= " %%V in ('%%C --version 2^>^&1') do (
                for /f "tokens=1,2 delims=." %%A in ("%%V") do (
                    if %%A==3 if %%B GEQ 10 set "PYTHON_EXE=%%C"
                )
            )
        )
    )
)

:: Search common user install paths
if not defined PYTHON_EXE call :SEARCH_PATHS

if defined PYTHON_EXE (
    echo  [OK] Found: %PYTHON_EXE%
    goto :INSTALL_PACKAGES
)

:: ============================================================
::  STEP 2 - Download Python
:: ============================================================
echo  [!!] Python 3.10+ not found.
echo.
echo  [2/4] Downloading Python %PY_VER% (%ARCH%)...

set "DL_URL=%PY_URL_64%"
if "%ARCH%"=="x86" set "DL_URL=%PY_URL_32%"

:: Try curl first, then PowerShell
curl --version >nul 2>&1
if %errorlevel%==0 (
    echo  Using curl...
    curl -L --progress-bar --retry 3 -o "%DL_FILE%" "%DL_URL%"
    goto :CHECK_DL
)

powershell -Command "exit 0" >nul 2>&1
if %errorlevel%==0 (
    echo  Using PowerShell...
    powershell -NoProfile -Command "$p='%DL_FILE%';$u='%DL_URL%';[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;(New-Object Net.WebClient).DownloadFile($u,$p);Write-Host 'Done'"
    goto :CHECK_DL
)

echo  [ERR] No download tool available.
echo        Download manually: https://www.python.org/downloads/
goto :FAIL

:CHECK_DL
if not exist "%DL_FILE%" (
    echo  [ERR] Download failed. Check internet connection.
    goto :FAIL
)
echo  [OK] Downloaded successfully.

:: ============================================================
::  STEP 3 - Install Python
:: ============================================================
echo.
echo  [3/4] Installing Python %PY_VER%...
echo        (This may take a minute, a progress window will appear)

"%DL_FILE%" InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1 Include_test=0 Include_doc=0 /passive

if %errorlevel% neq 0 (
    echo  [ERR] Installer failed with code %errorlevel%
    del /f /q "%DL_FILE%" >nul 2>&1
    goto :FAIL
)
del /f /q "%DL_FILE%" >nul 2>&1
echo  [OK] Python installed.

:: Refresh PATH from registry for this session
call :REFRESH_PATH

:: Search again after install
call :SEARCH_PATHS

if not defined PYTHON_EXE (
    echo  [ERR] Python installed but not found. Restart this bat file.
    goto :FAIL
)
echo  [OK] Found after install: %PYTHON_EXE%

:: ============================================================
::  STEP 4 - Install packages
:: ============================================================
:INSTALL_PACKAGES
echo.
echo  [4/4] Checking required packages...

"%PYTHON_EXE%" -m pip install --upgrade pip --quiet --no-warn-script-location 2>nul

for %%P in (%PACKAGES%) do (
    "%PYTHON_EXE%" -c "import %%P" >nul 2>&1
    if !errorlevel! neq 0 (
        echo  Installing %%P...
        "%PYTHON_EXE%" -m pip install %%P --quiet --no-warn-script-location
        if !errorlevel! neq 0 (
            echo  Retry %%P without quiet...
            "%PYTHON_EXE%" -m pip install %%P
        )
    ) else (
        echo  [OK] %%P
    )
)

:: pywin32 post-install (registers COM components)
"%PYTHON_EXE%" -c "import win32api" >nul 2>&1
if %errorlevel% neq 0 (
    echo  Running pywin32 post-install hook...
    for /f "usebackq delims=" %%X in (`"%PYTHON_EXE%" -c "import sys,os;print(os.path.join(sys.exec_prefix,'Scripts','pywin32_postinstall.py'))"`) do (
        if exist "%%X" "%PYTHON_EXE%" "%%X" -install >nul 2>&1
    )
)

:: ============================================================
::  Launch app
:: ============================================================
echo.
echo  +----------------------------------------------------------+
echo  ^|  [OK] All done. Starting Driver Reinstaller...          ^|
echo  +----------------------------------------------------------+
echo.

if not exist "%APP%" (
    echo  [ERR] driver_reinstaller.py not found next to this bat!
    echo        Both files must be in the same folder.
    goto :FAIL
)

"%PYTHON_EXE%" "%APP%"
if %errorlevel% neq 0 (
    echo.
    echo  [!!] App crashed with code %errorlevel%
    pause
)
endlocal
exit /b 0

:: ============================================================
::  :SEARCH_PATHS - check known Python install dirs
:: ============================================================
:SEARCH_PATHS
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python310\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
) do (
    if not defined PYTHON_EXE (
        if exist %%~P (
            set "PYTHON_EXE=%%~P"
        )
    )
)
goto :EOF

:: ============================================================
::  :REFRESH_PATH - re-read user PATH from registry
:: ============================================================
:REFRESH_PATH
:: Add the most common Python313 user path explicitly
if exist "%LOCALAPPDATA%\Programs\Python\Python313" (
    set "PATH=%LOCALAPPDATA%\Programs\Python\Python313;%LOCALAPPDATA%\Programs\Python\Python313\Scripts;%PATH%"
)
:: Also merge user PATH from registry via PowerShell
for /f "usebackq delims=" %%E in (`powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable('PATH','User')" 2^>nul`) do (
    if not "%%E"=="" set "PATH=%%E;%PATH%"
)
goto :EOF

:FAIL
echo.
echo  Setup failed. See messages above.
pause
endlocal
exit /b 1
