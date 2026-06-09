@echo off
if not exist venv (
    echo Virtual environment not found. Please run setup.bat first.
    pause
    exit /b
)
echo Activating virtual environment...
call venv\Scripts\activate.bat
echo Starting the Bluff game server...
python Data\app.py
pause
