@echo off
rem Lain AI - double-click to start
title Lain AI
"%~dp0runtime\win\python.exe" -I -X utf8 "%~dp0app\lain.py"
if errorlevel 1 pause
