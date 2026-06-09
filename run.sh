#!/bin/bash

if [ ! -d "venv" ]; then
    echo "Virtual environment not found. Please run ./setup.sh first."
    exit 1
fi

echo "Activating virtual environment..."
source venv/bin/activate

echo "Starting the Bluff game server..."
python Data/app.py
