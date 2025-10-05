@echo off
REM Aggancia 12 regole "meta" a static\data\sinapsi_rules.json
REM Usa Python già installato sul PC.

setlocal enabledelayedexpansion
set TARGET=static\data\sinapsi_rules.json

if not exist "%TARGET%" (
  echo [ERRORE] Non trovo %TARGET% . Esegui questo .bat dalla cartella del progetto (dove c'è app.py).
  pause
  exit /b 2
)

REM Prova con "python", se fallisce prova con "py -3"
python patch_rules.py "%TARGET%"
if errorlevel 1 (
  echo.
  echo (Riprovo con "py -3"...)
  py -3 patch_rules.py "%TARGET%"
)

echo.
echo --- Fatto. Se vedi "[RISULTATO] Aggiunte:" è andato a buon fine. ---
pause
