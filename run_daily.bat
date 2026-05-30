@echo off
REM ============================================================
REM  run_daily.bat - BYOCORE 일간 리포트 자동 실행 (Windows 작업 스케줄러용)
REM    python -m src.reporter  (어제 매출 집계 -> 카카오 '나에게 보내기')
REM    실행 로그: logs\daily_YYYYMMDD.log  (성공/에러 기록)
REM ============================================================
setlocal

REM --- 한글 깨짐 방지: UTF-8 모드 ---
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM --- 스크립트(프로젝트 루트) 폴더로 이동: 스케줄러가 다른 위치에서 호출해도 안전 ---
cd /d "%~dp0"

REM --- 로그 폴더 준비 ---
if not exist "logs" mkdir "logs"

REM --- 로그 파일 날짜(YYYYMMDD): 로캘 무관하게 PowerShell 로 산출 ---
set "TODAY="
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%i"
if not defined TODAY set "TODAY=00000000"
set "LOGFILE=logs\daily_%TODAY%.log"

REM --- 실행 헤더 기록 ---
echo.>>"%LOGFILE%"
echo ============================================================>>"%LOGFILE%"
echo [%date% %time%] run_daily.bat 시작>>"%LOGFILE%"
echo ------------------------------------------------------------>>"%LOGFILE%"

REM --- 리포터 실행: stdout+stderr 모두 로그에 append ---
python -m src.reporter >>"%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"

REM --- 결과 판정/기록 ---
echo ------------------------------------------------------------>>"%LOGFILE%"
if "%RC%"=="0" (
    echo [%date% %time%] 결과: 성공 ^(exit 0^)>>"%LOGFILE%"
    echo 성공: 리포트 발송 완료. 로그 = %LOGFILE%
) else (
    echo [%date% %time%] 결과: 에러 ^(exit %RC%^)>>"%LOGFILE%"
    echo 에러: exit %RC%. 로그 확인 = %LOGFILE%
)

endlocal & exit /b %RC%
