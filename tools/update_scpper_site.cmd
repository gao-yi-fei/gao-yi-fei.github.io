@echo off
chcp 65001 >nul
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "D:\downloads\lab4\tools\update_scpper_site.ps1" %*
