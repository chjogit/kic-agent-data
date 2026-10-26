@echo off
REM 장중 여론 펄스 (작업 스케줄러: 평일 09:00~15:30, 15분 간격). 정규장 밖이면 스스로 건너뜁니다.
cd /d "%~dp0.."
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TS=%%i
set PY=python
if exist ".venv\Scripts\python.exe" set PY=".venv\Scripts\python.exe"
set PYTHONIOENCODING=utf-8
echo ===== %TIME% >> "logs\pulse_%TS%.log"
%PY% -m kic_data.cli pulse --market-hours >> "logs\pulse_%TS%.log" 2>&1
forfiles /p logs /m pulse_*.log /d -14 /c "cmd /c del @path" >nul 2>&1
exit /b %ERRORLEVEL%
