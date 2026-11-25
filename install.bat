@echo off
rem Lain AI installer - double-click to start
title Lain AI installer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
