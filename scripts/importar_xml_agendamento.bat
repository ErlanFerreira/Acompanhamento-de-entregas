@echo off
rem Importa sozinho os XML de NF da pasta para o Agendamento de Entregas
rem (ver scripts\importar_xml_agendamento.py). Deixe a janela aberta.
cd /d "%~dp0\.."
title Importacao de XML - Agendamento de Entregas
python scripts\importar_xml_agendamento.py %*
pause
