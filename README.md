# bot_n8n
# Python Automation Project

This project contains a Python script that runs automatically using GitHub Actions.

## Setup Instructions

1. Fork this repository
2. Go to your repository's Settings > Secrets and Variables > Actions
3. Add the following secrets:
   - `SERPAPI_KEY`: Your SerpAPI key
   - `GEMINI_API_KEY`: Your Gemini API key

## Features

- Automated Python script execution
- Runs every 6 hours automatically
- Uses GitHub Actions for automation
- Secure API key management

## Requirements

- Python 3.10 or higher
- Required packages are listed in `requirements.txt`

## Local Development

1. Clone the repository
2. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: .\venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the script:
   ```bash
   python app.py
   ``` 