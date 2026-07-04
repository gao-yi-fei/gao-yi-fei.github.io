@echo off
chcp 65001 >nul
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -STA -File "D:\downloads\lab4\tools\update_scpper_site_gui.ps1" %*
