@echo off
chcp 65001 >nul
title Atenea Local
cd /d "%~dp0"
set "PYTHONUTF8=1"
where python >nul 2>&1
if not errorlevel 1 (
    python serve.py %*
) else (
    py -3 serve.py %*
)
if errorlevel 1 (
    echo.
    echo No s'ha pogut iniciar el servidor d'Atenea. Comprova que Python estigui instal·lat.
    pause
)
