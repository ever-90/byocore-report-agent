@echo off
REM ============================================================
REM  run_sales_scan.bat - BYOCORE Sales Daily Scan + Dashboard
REM  (Windows Task Scheduler - daily 09:00)
REM
REM  [1] Sales full scan -> scan_result.json + scan_summary.json
REM      (Naver API ~37 calls + GEO sheet, approx 3-5 min)
REM  [2] Report dashboard rebuild -> docs/index.html
REM  [3] git add/commit/push -> GitHub Pages auto deploy
REM  [4] Supervisor batch INCREMENTAL -> prescribe new/changed risks only (cost gate)
REM  [5] Telegram daily push -> scan summary to phone (read-only alert)
REM
REM  Log: logs\sales_scan_YYYYMMDD.log
REM
REM  RC design:
REM    [1] scan RC is the final exit code.
REM    [2][3] run independently (failures do NOT change RC).
REM    If [1] fails, [2][3] still run (old scan_summary used).
REM    If [2] fails, [3] is skipped.
REM
REM  This bat is FULLY INDEPENDENT of run_weekly/daily/monthly.bat.
REM  Python absolute path (v3.13, collision prevention).
REM  Naver API: ~37 calls/run. Daily. [4] designer LLM gated by incremental ledger.
REM ============================================================
setlocal

chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "PYTHON=C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe"
set "SALES_DIR=C:\Users\Administrator\byocore-sales-agent"
set "REPORT_DIR=C:\Users\Administrator\byocore-report-agent"

cd /d "%REPORT_DIR%"
if errorlevel 1 (
    echo [FATAL] Cannot cd to REPORT_DIR: %REPORT_DIR%
    endlocal & exit /b 1
)

if not exist "logs" mkdir "logs"

set "TODAY="
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%i"
if not defined TODAY set "TODAY=00000000"
set "LOGFILE=%REPORT_DIR%\logs\sales_scan_%TODAY%.log"
set "DATE_ISO=%TODAY:~0,4%-%TODAY:~4,2%-%TODAY:~6,2%"

echo.>>"%LOGFILE%"
echo ============================================================>>"%LOGFILE%"
echo [%date% %time%] run_sales_scan.bat start>>"%LOGFILE%"
echo   SALES_DIR : %SALES_DIR%>>"%LOGFILE%"
echo   REPORT_DIR: %REPORT_DIR%>>"%LOGFILE%"
echo ------------------------------------------------------------>>"%LOGFILE%"

REM ================================================================
REM  [1] Sales scan (CORE - only this RC becomes exit code)
REM      Failure -> [2][3] still run with old scan_summary.json
REM ================================================================
echo [%date% %time%] [1] Sales scan start (approx 3-5 min)...>>"%LOGFILE%"
cd /d "%SALES_DIR%"
if errorlevel 1 (
    echo [%date% %time%] [1] Cannot cd to SALES_DIR>>"%LOGFILE%"
    set "RC=1"
    goto :STEP2
)
"%PYTHON%" scan.py >>"%LOGFILE%" 2>&1
set "RC=%ERRORLEVEL%"
cd /d "%REPORT_DIR%"

echo ------------------------------------------------------------>>"%LOGFILE%"
if "%RC%"=="0" (
    echo [%date% %time%] [1] Sales scan OK exit 0>>"%LOGFILE%"
) else (
    echo [%date% %time%] [1] Sales scan ERROR exit %RC% - continuing with old summary>>"%LOGFILE%"
)

:STEP2
REM ================================================================
REM  [2] Dashboard rebuild (independent - failure does not change RC)
REM ================================================================
echo [%date% %time%] [2] Dashboard rebuild start...>>"%LOGFILE%"
"%PYTHON%" -m src.dashboard >>"%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] [2] Dashboard failed - git push skipped>>"%LOGFILE%"
    goto :END_GIT
)
echo [%date% %time%] [2] Dashboard rebuild OK>>"%LOGFILE%"

REM ================================================================
REM  [3] GitHub Pages push (independent - failure does not change RC)
REM ================================================================
echo [%date% %time%] [3] git add/commit/push start (%DATE_ISO%)>>"%LOGFILE%"

git add docs/index.html >>"%LOGFILE%" 2>&1

git diff --cached --quiet
if errorlevel 1 (
    git commit -m "chore: sales dashboard update %DATE_ISO%" >>"%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo [%date% %time%] [3] git commit failed - push skipped>>"%LOGFILE%"
        goto :END_GIT
    )
    git push >>"%LOGFILE%" 2>&1
    if errorlevel 1 (
        echo [%date% %time%] [3] git push failed - ignored>>"%LOGFILE%"
    ) else (
        echo [%date% %time%] [3] git push OK - GitHub Pages deployed>>"%LOGFILE%"
    )
) else (
    echo [%date% %time%] [3] docs/index.html unchanged - push skipped>>"%LOGFILE%"
)

:END_GIT
REM ================================================================
REM  [4] Supervisor batch INCREMENTAL (only if sales scan [1] succeeded)
REM      Diagnose all risk products + prescribe ONLY new/changed ones (cost gate).
REM      Already-prescribed (same own_facts hash) -> designer skipped (0 LLM).
REM      Skipped entirely if scan failed (avoid stale scan_summary).
REM      Does NOT change final exit code (scan RC preserved).
REM      --batch-incremental exit: 1 = all designer calls failed, 0 = otherwise.
REM ================================================================
set "SUPERVISOR_DIR=C:\Users\Administrator\byocore-supervisor-agent"
set "BATCH_RESULT=%SUPERVISOR_DIR%\data\batch_result.json"

if not "%RC%"=="0" (
    echo [%date% %time%] [4] Batch SKIPPED - scan failed ^(RC=%RC%^), avoid stale data>>"%LOGFILE%"
    goto :END_ALL
)

echo [%date% %time%] [4] Supervisor batch (incremental) start ^(new/changed only^)...>>"%LOGFILE%"
cd /d "%SUPERVISOR_DIR%"
if errorlevel 1 (
    echo [%date% %time%] [4][WARN] Cannot cd to SUPERVISOR_DIR - batch skipped>>"%LOGFILE%"
    cd /d "%REPORT_DIR%"
    goto :END_ALL
)
"%PYTHON%" -m src.supervisor --batch-incremental >>"%LOGFILE%" 2>&1
set "BRC=%ERRORLEVEL%"
cd /d "%REPORT_DIR%"

if "%BRC%"=="0" (
    echo [%date% %time%] [4] Batch OK ^(BRC=0^)>>"%LOGFILE%"
) else if "%BRC%"=="1" (
    echo [%date% %time%] [4][WARN] Batch all-fail ^(BRC=1^) - every designer call failed, check log above>>"%LOGFILE%"
) else (
    echo [%date% %time%] [4][WARN] Batch unexpected exit ^(BRC=%BRC%^)>>"%LOGFILE%"
)

if exist "%BATCH_RESULT%" (
    echo [%date% %time%] [4] batch_result.json present: %BATCH_RESULT%>>"%LOGFILE%"
) else (
    echo [%date% %time%] [4][WARN] batch_result.json missing - batch may have crashed>>"%LOGFILE%"
)

:END_ALL
REM ================================================================
REM  [4.5] GEO sync (READ-ONLY, independent - failure never blocks [5])
REM        .env 단일출처: GEO_SHEET_ID 를 로컬 임시변수(_GEO_SID)로만 읽어
REM        --sheet 로 그 자리에서 전달. 전역 set 안 함(후속 env 오염 0).
REM        && 제거 -> if/goto 분기로 단계격리 복원.
REM ================================================================
echo [%date% %time%] [4.5] GEO sync start>>"%LOGFILE%"
set "_GEO_SID="
for /f "tokens=2 delims==" %%g in ('findstr /b "GEO_SHEET_ID=" "%SALES_DIR%\.env"') do set "_GEO_SID=%%g"
cd /d "%SUPERVISOR_DIR%"
if errorlevel 1 (
    echo [%date% %time%] [4.5][WARN] Cannot cd to SUPERVISOR_DIR - sync skipped>>"%LOGFILE%"
    cd /d "%REPORT_DIR%"
    goto :STEP5
)
if not defined _GEO_SID (
    echo [%date% %time%] [4.5][WARN] GEO_SHEET_ID not found in .env - sync skipped>>"%LOGFILE%"
    cd /d "%REPORT_DIR%"
    goto :STEP5
)
"%PYTHON%" -m src.supervisor --sync-geo --sheet %_GEO_SID% >>"%LOGFILE%" 2>&1
set "SRC=%ERRORLEVEL%"
cd /d "%REPORT_DIR%"
set "_GEO_SID="
if "%SRC%"=="0" (
    echo [%date% %time%] [4.5] GEO sync OK>>"%LOGFILE%"
) else (
    echo [%date% %time%] [4.5][WARN] GEO sync failed ^(SRC=%SRC%^) - ignored ^(does not block push^)>>"%LOGFILE%"
)
:STEP5

REM ================================================================
REM  [5] Telegram daily push (READ-ONLY alert — always runs, RC unchanged)
REM ================================================================
echo [%date% %time%] [5] Telegram push start>>"%LOGFILE%"
"C:\Users\Administrator\AppData\Local\Programs\Python\Python313\python.exe" "C:\Users\Administrator\byocore-telegram-bot\push_daily.py" >>"%LOGFILE%" 2>&1
if errorlevel 1 (
    echo [%date% %time%] [5][WARN] Telegram push failed - check token/network>>"%LOGFILE%"
) else (
    echo [%date% %time%] [5] Telegram push OK>>"%LOGFILE%"
)

echo ============================================================>>"%LOGFILE%"
echo [%date% %time%] run_sales_scan.bat end (exit %RC%)>>"%LOGFILE%"

endlocal & exit /b %RC%

