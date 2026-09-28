@echo off
REM Builds politician_scrapers.exe and budget_scrapers.exe from this
REM folder's scripts using PyInstaller. Run this from a Windows command
REM prompt, with this folder as the current directory.
REM
REM First-time setup (only needed once):
REM     pip install openpyxl pyinstaller
REM
REM Then just run:
REM     build_exes.bat
REM
REM Output lands in .\dist\politician_scrapers.exe and
REM .\dist\budget_scrapers.exe -- each is a single self-contained file,
REM safe to copy anywhere (doesn't need Python installed to run).

echo Building politician_scrapers.exe ...
pyinstaller --onefile --name politician_scrapers ^
    --console ^
    --hidden-import scrape_federal ^
    --hidden-import scrape_provincial_territorial ^
    --hidden-import scrape_local ^
    --hidden-import scrape_all ^
    --hidden-import lookup_politician ^
    --hidden-import scrape_votes ^
    --hidden-import scrape_careers ^
    --hidden-import changes_digest ^
    --hidden-import make_maps ^
    --hidden-import openparliament_enrich ^
    --hidden-import represent_common ^
    cli_politicians.py
if errorlevel 1 goto :error

echo.
echo Building budget_scrapers.exe ...
pyinstaller --onefile --name budget_scrapers ^
    --console ^
    --hidden-import scrape_budget_federal ^
    --hidden-import scrape_budget_office_spending ^
    --hidden-import scrape_budget_provincial_territorial ^
    --hidden-import scrape_budget_local ^
    --hidden-import scrape_all_budgets ^
    --hidden-import budget_common ^
    --hidden-import represent_common ^
    cli_budgets.py
if errorlevel 1 goto :error

echo.
echo Done. Executables are in the "dist" folder:
echo   dist\politician_scrapers.exe
echo   dist\budget_scrapers.exe
echo.
echo Try:  dist\politician_scrapers.exe federal
echo       dist\budget_scrapers.exe federal
goto :eof

:error
echo.
echo Build failed -- see the PyInstaller output above for details.
exit /b 1
