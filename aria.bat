@echo off
setlocal EnableExtensions
rem ===========================================================================
rem  Aria - one-click launcher for Windows
rem
rem  Just double-click this file. It works from anywhere (Downloads, Desktop...):
rem    - downloads Aria into %USERPROFILE%\Aria if it isn't next to this file
rem    - starts Docker Desktop if it isn't running
rem    - creates the settings file (.env) with secure random secrets
rem    - asks for an AI API key the first time (Notepad opens)
rem    - starts Aria and opens it in your browser
rem    - puts an "Aria" shortcut on your Desktop
rem
rem  Optional commands (from a terminal):
rem    aria.bat stop     stop Aria (your data is kept)
rem    aria.bat logs     follow the server logs (Ctrl+C to leave)
rem    aria.bat status   show the running containers
rem    aria.bat update   download the latest version (keeps your .env), rebuild, start
rem ===========================================================================
set "REPO_ZIP=https://github.com/siv151992-lang/MY-AI/archive/refs/heads"
set "BRANCH=claude/wonderful-darwin-52ztwt"
set "URL=http://localhost:3000"
set "CMD=%~1"

rem --- Where is the project? Next to this file, or in %USERPROFILE%\Aria -----
if exist "%~dp0docker-compose.yml" (
  set "PROJECT=%~dp0"
) else (
  set "PROJECT=%USERPROFILE%\Aria"
)
if "%PROJECT:~-1%"=="\" set "PROJECT=%PROJECT:~0,-1%"

if /i "%CMD%"=="update" goto :download
if not exist "%PROJECT%\docker-compose.yml" goto :download
goto :have_project

:download
echo [*] Downloading the latest Aria into "%PROJECT%" ...
set "DL=%TEMP%\aria-download"
if exist "%DL%" rmdir /s /q "%DL%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference = 'Stop'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12;" ^
  "New-Item -ItemType Directory -Force '%DL%' | Out-Null; $zip = Join-Path '%DL%' 'aria.zip';" ^
  "try { Invoke-WebRequest -UseBasicParsing '%REPO_ZIP%/%BRANCH%.zip' -OutFile $zip } catch { Write-Host '    (feature branch not found, using main)'; Invoke-WebRequest -UseBasicParsing '%REPO_ZIP%/main.zip' -OutFile $zip };" ^
  "Expand-Archive -Path $zip -DestinationPath (Join-Path '%DL%' 'src') -Force"
if errorlevel 1 (
  echo [X] Download failed. Check your internet connection and try again.
  goto :fail
)
set "SRC="
for /d %%D in ("%DL%\src\*") do set "SRC=%%D"
if not defined SRC (
  echo [X] The download was empty.
  goto :fail
)
if not exist "%PROJECT%" mkdir "%PROJECT%"
rem Copy the code over; never overwrite your settings (.env)
robocopy "%SRC%" "%PROJECT%" /E /XF .env /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
  echo [X] Could not copy the files into "%PROJECT%".
  goto :fail
)
rmdir /s /q "%DL%" >nul 2>&1
echo [*] Aria files are in "%PROJECT%"

:have_project
cd /d "%PROJECT%"

rem --- Docker must be installed and running ----------------------------------
where docker >nul 2>&1
if errorlevel 1 (
  echo [X] Docker Desktop is not installed. It is required to run Aria.
  echo     Opening the download page. Install it, restart your PC if asked, then double-click this file again.
  start "" "https://www.docker.com/products/docker-desktop/"
  goto :fail
)
docker info >nul 2>&1
if not errorlevel 1 goto :docker_ok
set "DD=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if exist "%DD%" (
  echo [*] Starting Docker Desktop...
  start "" "%DD%"
) else (
  echo [!] Docker is not running. Please start Docker Desktop.
)
echo [*] Waiting for Docker to be ready. This can take a minute or two...
set /a TRIES=0
:wait_docker
timeout /t 3 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto :docker_ok
set /a TRIES+=1
if %TRIES% lss 80 goto :wait_docker
echo [X] Docker did not start within 4 minutes. Open Docker Desktop, wait until it says it is running, then try again.
goto :fail

:docker_ok
docker compose version >nul 2>&1
if errorlevel 1 (
  set "DC=docker-compose"
) else (
  set "DC=docker compose"
)

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
  echo [*] First run: creating your settings file .env with secure random secrets...
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
)
findstr /r /c:"^JWT_SECRET=change-me" ".env" >nul && (
  echo [X] JWT_SECRET in .env is still the placeholder. Delete "%PROJECT%\.env" and run this file again.
  goto :fail
)

rem --- At least one AI provider is needed ------------------------------------
call :check_provider
if not defined HAS_PROVIDER (
  echo.
  echo [!] Aria needs an AI model. Notepad will now open your settings file:
  echo       - paste your key after ANTHROPIC_API_KEY=  or  OPENAI_API_KEY=
  echo       - or, for free local models with Ollama, delete the "# " before OLLAMA_BASE_URL
  echo       Optional: TAVILY_API_KEY or BRAVE_API_KEY turns on web search and Deep research.
  echo     Save the file and close Notepad to continue.
  echo.
  start /wait notepad ".env"
  call :check_provider
)
if not defined HAS_PROVIDER (
  echo [!] Still no AI key. Aria will start, but chat won't work until you add one to
  echo     "%PROJECT%\.env" and run this file again.
)

rem --- Build and start --------------------------------------------------------
echo [*] Starting Aria. The first time this downloads and builds everything and can take 5-15 minutes...
%DC% up -d --build
if errorlevel 1 (
  echo [X] Docker could not start Aria. See the messages above.
  goto :fail
)

echo [*] Waiting for Aria to answer on %URL% ...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "for ($i = 0; $i -lt 90; $i++) { try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 '%URL%/login' | Out-Null; exit 0 } catch { Start-Sleep -Seconds 2 } }; exit 1"
if errorlevel 1 (
  echo [!] Aria did not respond within 3 minutes. It may still be starting.
  echo     Try %URL% in your browser in a minute, or run "aria.bat logs" in "%PROJECT%".
  goto :fail
)

call :shortcut
echo.
echo [OK] Aria is running at %URL%
echo      Next time, just double-click "Aria" on your Desktop.
start "" "%URL%"
timeout /t 5 >nul
goto :end

:stop
%DC% down
echo [OK] Aria stopped. Your chats, files and settings are kept.
goto :end

:logs
%DC% logs -f --tail 200
goto :end

:status
%DC% ps
goto :end

rem --- helpers ---------------------------------------------------------------
:check_provider
set "HAS_PROVIDER="
findstr /r /c:"^ANTHROPIC_API_KEY=." ".env" >nul && set "HAS_PROVIDER=1"
findstr /r /c:"^OPENAI_API_KEY=." ".env" >nul && set "HAS_PROVIDER=1"
findstr /r /c:"^OLLAMA_BASE_URL=." ".env" >nul && set "HAS_PROVIDER=1"
exit /b 0

:shortcut
if exist "%USERPROFILE%\Desktop\Aria.lnk" exit /b 0
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Aria.lnk');" ^
  "$s.TargetPath = '%PROJECT%\aria.bat'; $s.WorkingDirectory = '%PROJECT%'; $s.Description = 'Start Aria'; $s.Save()" >nul 2>&1
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1

:end
endlocal
exit /b 0
