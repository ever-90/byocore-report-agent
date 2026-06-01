@echo off
REM ============================================================
REM  run_daily.bat - BYOCORE 일간 리포트 + 대시보드 자동 실행
REM    [1] python -m src.reporter  (어제 매출 -> 카카오 나에게 보내기)
REM    [2] python -m src.dashboard (docs/index.html 갱신)
REM    [3] git add/commit/push     (GitHub Pages 자동 배포)
REM    로그: logs\daily_YYYYMMDD.log
REM    ★ 리포트 RC 만 exit code 반환. 대시보드/git 실패는 무방.
REM ============================================================
setlocal

REM --- 한글 깨짐 방지: UTF-8 모드 ---
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

REM --- 스크립트(프로젝트 루트) 폴더로 이동 ---
cd /d "%~dp0"

REM --- 로그 폴더 준비 ---
if not exist "logs" mkdir "logs"

REM --- 로그 날짜 산출 ---
set "TODAY="
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%i"
if not defined TODAY set "TODAY=00000000"
set "LOGFILE=logs\daily_%TODAY%.log"
set "DATE_ISO=%TODAY:~0,4%-%TODAY:~4,2%-%TODAY:~6,2%"

REM --- 실행 헤더 ---
echo.>>"%LOGFILE%"
echo ============================================================>>"%LOGFILE%"
echo [%date% %time%] run_daily.bat 시작>>"%LOGFILE%"
echo ------------------------------------------------------------>>"%LOGFILE%"

REM ================================================================
REM  [1] 카카오 리포트 발송 (CORE -- 이 RC 만 최종 exit code 로 반환)
REM ================================================================
"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe" -m src.reporter >>"%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"

echo ------------------------------------------------------------>>"%LOGFILE%"
if "%RC%"=="0" (
    echo [%date% %time%] [1] 리포트 발송 성공>>"%LOGFILE%"
    echo 성공: 리포트 발송 완료. 로그 = %LOGFILE%
) else (
    echo [%date% %time%] [1] 리포트 발송 에러 ^(exit %RC%^)>>"%LOGFILE%"
    echo 에러: exit %RC%. 로그 확인 = %LOGFILE%
)

REM ================================================================
REM  [2] 대시보드 HTML 생성 (독립 -- 실패해도 RC 불변)
REM ================================================================
echo [%date% %time%] [2] 대시보드 생성 시작>>"%LOGFILE%"
"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe" -m src.dashboard >>"%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] [2] 대시보드 생성 실패 -- git push 스킵>>"%LOGFILE%"
    goto :END_GIT
)
echo [%date% %time%] [2] 대시보드 생성 성공>>"%LOGFILE%"

REM ================================================================
REM  [3] GitHub Pages push (독립 -- 실패해도 RC 불변)
REM ================================================================
echo [%date% %time%] [3] git add/commit/push 시작 ^(%DATE_ISO%^)>>"%LOGFILE%"

git add docs/index.html >>"%LOGFILE%" 2>&1

REM staged 변경 있을 때만 commit (없으면 "nothing to commit" 방지)
git diff --cached --quiet
if errorlevel 1 (
    git commit -m "chore: 대시보드 갱신 %DATE_ISO%" >>"%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo [%date% %time%] [3] git commit 실패 -- push 스킵>>"%LOGFILE%"
        goto :END_GIT
    )
    git push >>"%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo [%date% %time%] [3] git push 실패 -- 무시>>"%LOGFILE%"
    ) else (
        echo [%date% %time%] [3] git push 성공>>"%LOGFILE%"
    )
) else (
    echo [%date% %time%] [3] docs/index.html 변경 없음 -- push 스킵>>"%LOGFILE%"
)

:END_GIT
echo ============================================================>>"%LOGFILE%"
endlocal & exit /b %RC%
