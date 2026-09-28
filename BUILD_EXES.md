# Building the .exe files (Windows)

This has to be built on Windows -- PyInstaller compiles for whatever OS
it's run on, it doesn't cross-compile. So this step needs to happen on
your own Windows machine, not wherever these files came from.

## 1. One-time setup

Install Python 3 if you don't have it (python.org), then from a command
prompt **in this folder**:

```
pip install -r requirements.txt
```

This installs `openpyxl` (needed to write the .xlsx files) and
`pyinstaller` (the compiler).

## 2. Build

Still in this folder:

```
build_exes.bat
```

This produces two files:

```
dist\politician_scrapers.exe
dist\budget_scrapers.exe
```

Each is a single, self-contained file -- once built, you can copy just
that one file anywhere and run it without Python installed. It'll still
write its output to `~/Desktop/output` (your Windows user's Desktop),
same as the plain Python scripts.

## 3. Usage

Each exe bundles several of the original scripts; you pick which one
runs with a subcommand as the first argument. Everything after that is
passed straight through to that script, so every flag from the main
README works exactly the same.

```
politician_scrapers.exe federal
politician_scrapers.exe federal --no-enrich
politician_scrapers.exe provincial
politician_scrapers.exe local
politician_scrapers.exe local --filter Toronto
politician_scrapers.exe all
politician_scrapers.exe lookup "Jane Doe"
politician_scrapers.exe lookup "Jane Doe" --live --category federal

budget_scrapers.exe federal
budget_scrapers.exe federal --refresh
budget_scrapers.exe federal --since 2015
budget_scrapers.exe provincial
budget_scrapers.exe provincial --province ontario
budget_scrapers.exe local
budget_scrapers.exe local --municipality toronto
budget_scrapers.exe all
```

Run either exe with no arguments to see this list again.

## Notes

- Rebuilding: just re-run `build_exes.bat` any time you edit the `.py`
  files -- it always compiles from source, so the exes stay in sync.
- Antivirus / SmartScreen: PyInstaller-built exes are unsigned, so
  Windows Defender or your antivirus may flag a brand-new one the first
  time you run it (this is common for any freshly-compiled, unsigned
  executable, not specific to this project). "More info" -> "Run
  anyway" in the SmartScreen prompt, or add an exclusion, if you trust
  the source.
- If you'd rather not compile anything: `python cli_politicians.py
  federal` and `python cli_budgets.py federal` (etc.) run the exact
  same dispatcher directly, no build step needed.
