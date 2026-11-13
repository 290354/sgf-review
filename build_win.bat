@echo off
chcp 65001 >nul
title SGF Review - Windows 构建
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_win.ps1"
echo.
pause
