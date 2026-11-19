@echo off
setlocal EnableExtensions
rem ===========================================================================
rem  Aria - run WITHOUT Docker on Windows 10/11 (no admin rights needed)
rem
rem  Double-click this file. On first run it downloads private, portable copies of
rem  Python, Node.js, PostgreSQL and Git into the "runtime" folder next to Aria,
rem  creates the database, installs and builds Aria, starts it and opens the browser.
rem  Nothing is installed system-wide (except the Microsoft Visual C++ runtime, only
rem  if your PC doesn't have it yet).
rem
rem  Differences from the Docker version:
rem    - code execution is OFF (run_python, workspace terminal, table analysis): it
rem      can't be isolated safely on Windows without Docker
rem    - search uses keywords instead of semantic (pgvector) search
rem    - OCR works only if Tesseract is installed (optional)
rem
rem  Commands (from a terminal in this folder):
rem    aria-native.bat          start (and set up on first run)
rem    aria-native.bat stop     stop Aria and its database (data is kept in "data")
rem    aria-native.bat logs     open the logs folder and show the last log lines
rem    aria-native.bat update   download the latest Aria (keeps .env and data), rebuild, start
rem ===========================================================================
set "REPO_ZIP=https://github.com/siv151992-lang/MY-AI/archive/refs/heads/main.zip"
set "NODE_URL=https://nodejs.org/dist/v22.12.0/node-v22.12.0-win-x64.zip"
set "UV_URL=https://github.com/astral-sh/uv/releases/download/0.5.11/uv-x86_64-pc-windows-msvc.zip"
set "PG_URL=https://github.com/theseus-rs/postgresql-binaries/releases/download/16.6.0/postgresql-16.6.0-x86_64-pc-windows-msvc.zip"
set "GIT_URL=https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/MinGit-2.47.1-64-bit.zip"
set "VCREDIST_URL=https://aka.ms/vs/17/release/vc_redist.x64.exe"
set "PG_PORT=5433"
set "API_PORT=8000"
set "WEB_PORT=3000"
set "URL=http://127.0.0.1:%WEB_PORT%"
set "CMD=%~1"

rem --- Where is Aria? Next to this file, or in %USERPROFILE%\AriaNative -------
if exist "%~dp0backend\requirements.txt" (
  set "ROOT=%~dp0"
) else (
  set "ROOT=%USERPROFILE%\AriaNative"
)
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
set "RT=%ROOT%\runtime"
set "DATA=%ROOT%\data"
set "LOGS=%DATA%\logs"

if /i "%CMD%"=="stop" goto :stop
if /i "%CMD%"=="logs" goto :logs
if /i "%CMD%"=="update" goto :download
if not exist "%ROOT%\backend\requirements.txt" goto :download
goto :have_project

:download
echo [*] Downloading the latest Aria into "%ROOT%" ...
if /i "%CMD%"=="update" call :stop_all
set "DL=%TEMP%\aria-native-download"
if exist "%DL%" rmdir /s /q "%DL%"
call :fetch "%REPO_ZIP%" "%DL%" ""
if errorlevel 1 goto :fail
if not exist "%ROOT%" mkdir "%ROOT%"
rem Copy the code; never touch your settings (.env), data or the downloaded runtimes
robocopy "%DL%" "%ROOT%" /E /XF .env /XD data runtime /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo [X] Could not copy the files into "%ROOT%".
  goto :fail
)
rmdir /s /q "%DL%" >nul 2>&1
set "FORCE_BUILD=1"

:have_project
cd /d "%ROOT%"
for %%D in ("%RT%" "%DATA%" "%LOGS%" "%DATA%\uploads" "%DATA%\workspaces") do if not exist "%%~D" mkdir "%%~D"

rem --- PostgreSQL refuses to run with administrator rights -------------------------
net session >nul 2>&1
if not errorlevel 1 (
  echo [X] Please don't run this file as administrator: the database refuses to start with admin rights.
  echo     Close this window and double-click the file normally.
  goto :fail
)

rem --- Already running? Just open it ------------------------------------------
if /i not "%CMD%"=="update" (
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 '%URL%/login' | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 (
    echo [OK] Aria is already running at %URL%
    start "" "%URL%"
    goto :end
  )
)
for %%P in (%WEB_PORT% %API_PORT% %PG_PORT%) do (
  netstat -ano | findstr /r /c:"127.0.0.1:%%P .*LISTENING" /c:"0.0.0.0:%%P .*LISTENING" /c:"\[::\]:%%P .*LISTENING" >nul && (
    echo [X] Port %%P is already used by another program. If Aria is half-running, run "aria-native.bat stop";
    echo     if the Docker version of Aria is running, stop it with "aria.bat stop". Then run this file again.
    goto :fail
  )
)

rem --- Microsoft Visual C++ runtime - needed by PostgreSQL ----------------------
if not exist "%SystemRoot%\System32\msvcp140.dll" goto :vcredist
if not exist "%SystemRoot%\System32\vcruntime140.dll" goto :vcredist
goto :runtimes
:vcredist
echo [*] Installing the Microsoft Visual C++ runtime - Windows will ask for permission...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing '%VCREDIST_URL%' -OutFile (Join-Path $env:TEMP 'vc_redist.x64.exe')"
"%TEMP%\vc_redist.x64.exe" /install /quiet /norestart
if not exist "%SystemRoot%\System32\msvcp140.dll" (
  echo [X] The Visual C++ runtime could not be installed. Install it from %VCREDIST_URL% and try again.
  goto :fail
)

:runtimes
rem --- Portable runtimes (downloaded once into the runtime folder) --------------
call :fetch "%NODE_URL%" "%RT%\node" "node.exe" || goto :fail
call :fetch "%UV_URL%" "%RT%\uv" "uv.exe" || goto :fail
call :fetch "%PG_URL%" "%RT%\pgsql" "bin\pg_ctl.exe" || goto :fail
call :fetch "%GIT_URL%" "%RT%\git" "cmd\git.exe" || goto :fail
set "PATH=%RT%\venv\Scripts;%RT%\node;%RT%\pgsql\bin;%RT%\git\cmd;%PATH%"
if exist "%ProgramFiles%\Tesseract-OCR\tesseract.exe" set "PATH=%PATH%;%ProgramFiles%\Tesseract-OCR"

rem --- Python environment for the backend --------------------------------------
set "UV_PYTHON_INSTALL_DIR=%RT%\python"
set "UV_CACHE_DIR=%RT%\uv-cache"
set "UV_LINK_MODE=copy"
if not exist "%RT%\venv\Scripts\python.exe" (
  echo [*] Setting up Python...
  "%RT%\uv\uv.exe" venv "%RT%\venv" --python 3.12 --python-preference only-managed
  if errorlevel 1 (
    echo [X] Could not set up Python.
    goto :fail
  )
)
fc /b "backend\requirements.txt" "%RT%\requirements.installed" >nul 2>&1
if errorlevel 1 (
  echo [*] Installing the backend's Python packages...
  "%RT%\uv\uv.exe" pip install --python "%RT%\venv\Scripts\python.exe" -r backend\requirements.txt
  if errorlevel 1 (
    echo [X] Installing Python packages failed. See the messages above.
    goto :fail
  )
  copy /y "backend\requirements.txt" "%RT%\requirements.installed" >nul
)

rem --- Frontend: install and build -----------------------------------------------
set "BACKEND_URL=http://127.0.0.1:%API_PORT%"
set "NEXT_TELEMETRY_DISABLED=1"
cd /d "%ROOT%\frontend"
fc /b "package.json" "%RT%\package.installed" >nul 2>&1
if errorlevel 1 (
  echo [*] Installing the web app's packages...
  call npm install --no-audit --no-fund
  if errorlevel 1 (
    echo [X] npm install failed. See the messages above.
    goto :fail
  )
  copy /y "package.json" "%RT%\package.installed" >nul
  set "FORCE_BUILD=1"
)
if not exist ".next\standalone\server.js" set "FORCE_BUILD=1"
if defined FORCE_BUILD (
  echo [*] Building the web app - this takes a few minutes the first time...
  call npm run build
  if errorlevel 1 (
    echo [X] Building the web app failed. See the messages above.
    goto :fail
  )
  xcopy /e /i /y /q ".next\static" ".next\standalone\.next\static" >nul
  if exist "public" xcopy /e /i /y /q "public" ".next\standalone\public" >nul
)
cd /d "%ROOT%"

rem --- Settings file (.env) --------------------------------------------------------
if not exist ".env" (
  echo [*] First run: creating your settings file .env with secure random secrets...
  copy /y ".env.example" ".env" >nul
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$r = New-Object Security.Cryptography.RNGCryptoServiceProvider;" ^
  "function New-Secret { $b = New-Object byte[] 32; $r.GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') }) };" ^
  "$c = Get-Content '.env';" ^
  "$c = $c -replace '^JWT_SECRET=change-me.*', ('JWT_SECRET=' + (New-Secret)) -replace '^SANDBOX_TOKEN=change-me.*', ('SANDBOX_TOKEN=' + (New-Secret));" ^
  "if (-not ($c -match '^DATABASE_URL=')) { $c += 'DATABASE_URL=postgresql://aria:' + (New-Secret) + '@127.0.0.1:%PG_PORT%/aria' };" ^
  "$c | Set-Content '.env' -Encoding ascii;" ^
  "$m = [regex]::Match(($c -join [char]10), 'DATABASE_URL=postgresql://aria:([^@]+)@');" ^
  "Set-Content '%DATA%\pw.tmp' $m.Groups[1].Value -NoNewline -Encoding ascii"
if errorlevel 1 (
  echo [X] Could not prepare the settings file .env.
  goto :fail
)
set "PGPASSWORD="
for /f "usebackq delims=" %%P in ("%DATA%\pw.tmp") do set "PGPASSWORD=%%P"

rem --- AI provider key ---------------------------------------------------------------
call :check_provider
if not defined HAS_PROVIDER (
  echo.
  echo [!] Aria needs an AI model. Notepad will now open your settings file:
  echo       - paste your key after ANTHROPIC_API_KEY=  or  OPENAI_API_KEY=
  echo       - or, for free local models with Ollama, delete the "# " before OLLAMA_BASE_URL
  echo       Optional: TAVILY_API_KEY or BRAVE_API_KEY turns on web search and Deep research.
  echo.
  start "" notepad ".env"
  echo     When you have saved the file in Notepad, come back to this window and
  pause
  call :check_provider
)
if not defined HAS_PROVIDER (
  echo [!] Still no AI key. Aria will start, but chat won't work until you add one to
  echo     "%ROOT%\.env" and run this file again.
)

rem --- Database ------------------------------------------------------------------------
if not exist "%DATA%\pgdata\PG_VERSION" (
  echo [*] Creating the database...
  "%RT%\pgsql\bin\initdb.exe" -D "%DATA%\pgdata" -U aria --pwfile="%DATA%\pw.tmp" -A scram-sha-256 -E UTF8 --locale=C >"%LOGS%\initdb.log" 2>&1
  if errorlevel 1 (
    del /q "%DATA%\pw.tmp" >nul 2>&1
    echo [X] Creating the database failed. Details: "%LOGS%\initdb.log"
    goto :fail
  )
)
del /q "%DATA%\pw.tmp" >nul 2>&1
echo [*] Starting the database...
start "Aria database" /min "%RT%\pgsql\bin\postgres.exe" -D "%DATA%\pgdata" -p %PG_PORT% -c listen_addresses=127.0.0.1
set /a TRIES=0
:wait_db
"%RT%\pgsql\bin\pg_isready.exe" -h 127.0.0.1 -p %PG_PORT% -q
if not errorlevel 1 goto :db_ready
set /a TRIES+=1
if %TRIES% geq 60 (
  echo [X] The database did not start. Check the "Aria database" window on the taskbar.
  goto :fail
)
timeout /t 1 /nobreak >nul
goto :wait_db
:db_ready
"%RT%\pgsql\bin\psql.exe" -h 127.0.0.1 -p %PG_PORT% -U aria -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='aria'" | findstr "1" >nul
if errorlevel 1 "%RT%\pgsql\bin\createdb.exe" -h 127.0.0.1 -p %PG_PORT% -U aria aria
set "PGPASSWORD="

rem --- Start the backend and the web app ---------------------------------------------
echo [*] Starting Aria...
set "SANDBOX_URL=off"
set "UPLOAD_DIR=%DATA%\uploads"
set "WORKSPACE_ROOT=%DATA%\workspaces"
start "Aria backend" /D "%ROOT%" /min cmd /c ""%RT%\venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port %API_PORT% > "%LOGS%\backend.log" 2>&1"
set "PORT=%WEB_PORT%"
set "HOSTNAME=127.0.0.1"
start "Aria web" /D "%ROOT%\frontend\.next\standalone" /min cmd /c ""%RT%\node\node.exe" server.js > "%LOGS%\frontend.log" 2>&1"

echo [*] Waiting for Aria to answer on %URL% ...
powershell -NoProfile -Command ^
  "for ($i = 0; $i -lt 90; $i++) { try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 '%URL%/api/health'; exit 0 } catch { Start-Sleep -Seconds 2 } }; exit 1"
if errorlevel 1 (
  echo [X] Aria did not start within 3 minutes. The last lines of its logs:
  powershell -NoProfile -Command "Get-Content '%LOGS%\backend.log' -Tail 25 -ErrorAction SilentlyContinue; Get-Content '%LOGS%\frontend.log' -Tail 10 -ErrorAction SilentlyContinue"
  goto :fail
)

call :shortcut
echo.
echo [OK] Aria is running at %URL%  - without Docker.
echo      Code execution is off and search uses keywords in this mode.
echo      Three minimized windows keep it running: "Aria database", "Aria backend", "Aria web".
echo      To stop it: run "aria-native.bat stop" in "%ROOT%".
start "" "%URL%"
timeout /t 8 >nul
goto :end

rem ==================================================================================
:stop
cd /d "%ROOT%"
call :stop_all
echo [OK] Aria stopped. Your chats, files and settings are kept in "%DATA%".
goto :end

:stop_all
taskkill /FI "WINDOWTITLE eq Aria web*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq Aria backend*" /T /F >nul 2>&1
if exist "%RT%\pgsql\bin\pg_ctl.exe" if exist "%DATA%\pgdata\postmaster.pid" "%RT%\pgsql\bin\pg_ctl.exe" stop -D "%DATA%\pgdata" -m fast -w >nul 2>&1
taskkill /FI "WINDOWTITLE eq Aria database*" /T /F >nul 2>&1
exit /b 0

:logs
start "" "%LOGS%"
powershell -NoProfile -Command "foreach ($f in 'backend','frontend') { Write-Host ('==== ' + $f + ' ===='); Get-Content (Join-Path '%LOGS%' ($f + '.log')) -Tail 30 -ErrorAction SilentlyContinue }"
pause
goto :end

rem --- helpers ----------------------------------------------------------------------
rem :fetch URL DESTINATION MARKER  - download a zip and unpack it, unless MARKER already exists
:fetch
if not "%~3"=="" if exist "%~2\%~3" exit /b 0
echo [*] Downloading %~1 ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue';" ^
  "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12;" ^
  "$t = Join-Path $env:TEMP ('aria-' + [guid]::NewGuid()); $x = Join-Path $t 'x'; New-Item -ItemType Directory -Force $x | Out-Null;" ^
  "$z = Join-Path $t 'a.zip'; Invoke-WebRequest -UseBasicParsing '%~1' -OutFile $z;" ^
  "if (Get-Command tar.exe -ErrorAction SilentlyContinue) { tar.exe -xf $z -C $x; if ($LASTEXITCODE) { throw 'unpacking failed' } } else { Expand-Archive $z $x };" ^
  "$items = @(Get-ChildItem -Force $x); $src = if ($items.Count -eq 1 -and $items[0].PSIsContainer) { $items[0].FullName } else { $x };" ^
  "New-Item -ItemType Directory -Force '%~2' | Out-Null; Get-ChildItem -Force $src | Move-Item -Destination '%~2' -Force;" ^
  "Remove-Item $t -Recurse -Force"
if errorlevel 1 (
  echo [X] Download failed: %~1
  echo     Check your internet connection and try again.
  exit /b 1
)
exit /b 0

:check_provider
set "HAS_PROVIDER="
findstr /r /c:"^ANTHROPIC_API_KEY=." ".env" >nul && set "HAS_PROVIDER=1"
findstr /r /c:"^OPENAI_API_KEY=." ".env" >nul && set "HAS_PROVIDER=1"
findstr /r /c:"^OLLAMA_BASE_URL=." ".env" >nul && set "HAS_PROVIDER=1"
exit /b 0

:shortcut
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p = [Environment]::GetFolderPath('Desktop') + '\Aria (no Docker).lnk'; if (-not (Test-Path $p)) {" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($p); $s.TargetPath = '%ROOT%\aria-native.bat';" ^
  "$s.WorkingDirectory = '%ROOT%'; $s.Description = 'Start Aria without Docker'; $s.Save() }" >nul 2>&1
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1

:end
endlocal
exit /b 0
