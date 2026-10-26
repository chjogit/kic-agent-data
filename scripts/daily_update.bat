@echo off
REM 매일 최근분 갱신 (Windows 작업 스케줄러용). 수집 → 지표 재계산 → features.json 까지 한 번에.
REM 프로젝트 폴더의 .venv 를 우선 쓰고, 없으면 PATH 의 python 을 씁니다.
cd /d "%~dp0.."
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmm"') do set TS=%%i
set PY=python
if exist ".venv\Scripts\python.exe" set PY=".venv\Scripts\python.exe"
set PYTHONIOENCODING=utf-8
%PY% -m kic_data.cli daily-update >> "logs\daily_%TS%.log" 2>&1
REM 30일 지난 로그 정리
forfiles /p logs /m daily_*.log /d -30 /c "cmd /c del @path" >nul 2>&1
exit /b %ERRORLEVEL%
