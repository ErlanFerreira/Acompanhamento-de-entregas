@echo off
rem Liga o robo de emissao de CT-e deste PC (ver scripts\robo_emissao.py).
rem Deixe a janela aberta enquanto quiser que o robo trabalhe.
cd /d "%~dp0\.."
title Robo de emissao de CT-e
python scripts\robo_emissao.py %*
pause
