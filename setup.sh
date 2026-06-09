#!/bin/bash

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing requirements..."
export AIOHTTP_NO_EXTENSIONS=1
pip install -r Data/requirements.txt

echo "Setup complete!"
