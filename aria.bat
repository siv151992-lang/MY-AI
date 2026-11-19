@echo off
setlocal EnableExtensions
rem ===========================================================================
rem  Aria launcher for Windows (needs Docker Desktop)
rem
rem    aria.bat          set up (first run) and start Aria, then open the browser
rem    aria.bat stop     stop Aria (your data is kept)
rem    aria.bat logs     follow the server logs (Ctrl+C to leave)
rem    aria.bat status   show the running containers
rem    aria.bat update   rebuild after pulling new code, then start
rem ===========================================================================
cd /d "%~dp0"
set "URL=http://localhost:3000"

rem --- Docker must be installed and running ----------------------------------
where docker >nul 2>&1
if errorlevel 1 (
  echo [X] Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/
  goto :fail
)
docker info >nul 2>&1
if errorlevel 1 (
  echo [X] Docker is installed but not running. Start Docker Desktop, wait until it says "running", then try again.
  goto :fail
)
docker compose version >nul 2>&1
if errorlevel 1 (
  set "DC=docker-compose"
) else (
  set "DC=docker compose"
)

set "CMD=%~1"
if /i "%CMD%"=="stop"   goto :stop
if /i "%CMD%"=="logs"   goto :logs
if /i "%CMD%"=="status" goto :status
if /i "%CMD%"=="update" goto :start
if not "%CMD%"=="" (
  echo Unknown command "%CMD%". Use: aria.bat [stop ^| logs ^| status ^| update]
  goto :fail
)

:start
rem --- First run: create .env with random secrets ----------------------------
if not exist ".env" (
  if not exist ".env.example" (
    echo [X] .env.example is missing. Run this file from the Aria project folder.
    goto :fail
  )
  echo [*] First run: creating .env with freshly generated secrets...
  copy /y ".env.example" ".env" >nul
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$r = New-Object Security.Cryptography.RNGCryptoServiceProvider;" ^
    "function New-Secret { $b = New-Object byte[] 32; $r.GetBytes($b); -join ($b | ForEach-Object { $_.ToString('x2') }) };" ^
    "$jwt = New-Secret; $sbx = New-Secret;" ^
    "(Get-Content '.env') -replace '^JWT_SECRET=.*', ('JWT_SECRET=' + $jwt) -replace '^SANDBOX_TOKEN=.*', ('SANDBOX_TOKEN=' + $sbx) | Set-Content '.env' -Encoding ascii"
  if errorlevel 1 (
    echo [X] Could not generate secrets. Edit .env by hand: set JWT_SECRET and SANDBOX_TOKEN to long random strings.
    goto :fail
  )
  echo [*] Created .env
)

rem --- Refuse to start with the placeholder secrets --------------------------
findstr /r /c:"^JWT_SECRET=change-me" ".env" >nul && (
  echo [X] JWT_SECRET in .env is still the placeholder. Delete .env and run aria.bat again to generate one.
  goto :fail
)

rem --- At least one AI provider is needed ------------------------------------
set "HAS_PROVIDER="
findstr /r /c:"^ANTHROPIC_API_KEY=." ".env" >nul && set "HAS_PROVIDER=1"
findstr /r /c:"^OPENAI_API_KEY=." ".env" >nul && set "HAS_PROVIDER=1"
findstr /r /c:"^OLLAMA_BASE_URL=." ".env" >nul && set "HAS_PROVIDER=1"
if not defined HAS_PROVIDER (
  echo.
  echo [!] No AI provider is configured yet. Notepad will open .env:
  echo     - set ANTHROPIC_API_KEY=... or OPENAI_API_KEY=...
  echo     - or, for local models with Ollama, remove the "# " before OLLAMA_BASE_URL
  echo     Optional: TAVILY_API_KEY or BRAVE_API_KEY enables web search and Deep research.
  echo     Save the file and close Notepad to continue.
  echo.
  start /wait notepad ".env"
  set "HAS_PROVIDER="
  findstr /r /c:"^ANTHROPIC_API_KEY=." ".env" >nul && set "HAS_PROVIDER=1"
  findstr /r /c:"^OPENAI_API_KEY=." ".env" >nul && set "HAS_PROVIDER=1"
  findstr /r /c:"^OLLAMA_BASE_URL=." ".env" >nul && set "HAS_PROVIDER=1"
  if not defined HAS_PROVIDER (
    echo [!] Still no provider key. Aria will start, but chat will say no model is configured until you add one
    echo     to .env and run aria.bat again.
  )
)

rem --- Build and start --------------------------------------------------------
echo [*] Building and starting Aria. The first run downloads and builds images and can take several minutes...
%DC% up -d --build
if errorlevel 1 (
  echo [X] Docker Compose failed. See the messages above, or run: aria.bat logs
  goto :fail
)

echo [*] Waiting for Aria to answer on %URL% ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "for ($i = 0; $i -lt 90; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 '%URL%/login' | Out-Null; exit 0 } catch { Start-Sleep -Seconds 2 } }; exit 1"
if errorlevel 1 (
  echo [!] Aria did not respond within 3 minutes. It may still be starting; check with: aria.bat logs
  goto :fail
)

echo.
echo [OK] Aria is running at %URL%
echo      Stop it with: aria.bat stop     Logs: aria.bat logs
start "" "%URL%"
goto :end

:stop
%DC% down
echo [OK] Aria stopped. Your chats, files and settings are kept in Docker volumes.
goto :end

:logs
%DC% logs -f --tail 200
goto :end

:status
%DC% ps
goto :end

:fail
echo.
pause
endlocal
exit /b 1

:end
endlocal
exit /b 0
