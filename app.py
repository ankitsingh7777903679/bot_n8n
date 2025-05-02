from flask import Flask, render_template, request, jsonify
import requests
import google.generativeai as genai
from openai import OpenAI
import asyncio
import aiohttp
import json
from functools import lru_cache
import os
import pandas as pd
import tempfile
import PyPDF2
from bs4 import BeautifulSoup
from typing import Dict, Any, List

app = Flask(__name__)

# API keys
SERPAPI_KEY = "85e37aad579f759db20e1a54babd79a33d65bd700c60e871882353a90f9ae508"
GEMINI_API_KEY = "AIzaSyAPAaL1zlTQLYLqnOQDJqXfPDCVrsymVR0"  # Replace with your actual key
OPENAI_API_KEY = "your_openai_api_key"

# Load or initialize memory file
MEMORY_FILE = "memory.json"
conversation_history: List[Dict[str, str]] = []
gemini_history: List[Dict[str, Any]] = []

try:
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        conversation_history = json.load(f)
        gemini_history = [
            {"role": "user" if msg["role"] == "user" else "model", "parts": [{"text": msg["content"]}]}
            for msg in conversation_history
        ]
except FileNotFoundError:
    conversation_history = []
    gemini_history = []

# In-memory storage for processed external data
data_store: Dict[str, str] = {}

# Configure Google Gemini
try:
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-2.0-flash')
    gemini_chat = model.start_chat(history=gemini_history)
    use_gemini = True
except Exception as e:
    print(f"Error initializing Gemini: {e}")
    use_gemini = False
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
    except Exception as e:
        print(f"Error initializing OpenAI: {e}")
        client = None

# Process different types of external data sources
def process_data_source(source_type: str, source: Any) -> str:
    try:
        if source_type == "csv":
            df = pd.read_csv(source)
            return df.to_string()
        elif source_type == "pdf":
            reader = PyPDF2.PdfReader(source)
            text = ""
            for page in reader.pages:
                text += page.extract_text()
            return text
        elif source_type == "json":
            data = json.load(source)
            return json.dumps(data, indent=2, ensure_ascii=False)
        elif source_type == "text":
            return source.read().decode('utf-8')
        elif source_type == "website":
            response = requests.get(source)
            if "application/json" in response.headers.get("Content-Type", ""):
                return json.dumps(response.json(), indent=2, ensure_ascii=False)
            soup = BeautifulSoup(response.text, 'html.parser')
            return soup.get_text()
        else:
            return "Unsupported data type"
    except Exception as e:
        return f"Error processing {source_type}: {str(e)}"

# Load external data sources on startup
def load_external_data():
    # Load emp.csv from root directory
    csv_file = "emp.csv"
    if os.path.exists(csv_file):
        with open(csv_file, 'rb') as f:
            processed_data = process_data_source("csv", f)
            data_store["csv_emp.csv"] = processed_data
            
    csv_file = "student.csv"
    if os.path.exists(csv_file):
        with open(csv_file, 'rb') as f:
            processed_data = process_data_source("csv", f)
            data_store["csv_student.csv"] = processed_data

    else:
        print(f"Error: emp.csv not found in the root directory.")

    # Load data from a website link (replace with your desired URL)
    website_url = "https://example.com/company-details"  # Replace with your actual URL
    try:
        processed_data = process_data_source("website", website_url)
        data_store["website_" + website_url] = processed_data
    except Exception as e:
        print(f"Error fetching website data: {str(e)}")

# Load data when the app starts
load_external_data()

@lru_cache(maxsize=100)
async def fetch_serpapi_results(query: str, language: str = "hi") -> Dict[str, Any]:
    url = "https://serpapi.com/search"
    params = {
        "q": query,
        "api_key": SERPAPI_KEY,
        "hl": language,
        "gl": "in",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            return await response.json()

def get_gemini_response_text(response: Any) -> str:
    try:
        parts = response.candidates[0].content.parts
        output = []
        for part in parts:
            if getattr(part, "text", None):
                output.append(part.text)
            if getattr(part, "executable_code", None):
                output.append(f"\n```python\n{part.executable_code.code}\n```")
            if getattr(part, "code_execution_result", None):
                output.append(f"\n**Output:**\n{part.code_execution_result.output}")
        return "\n".join(output).strip()
    except Exception:
        if hasattr(response, "text"):
            return response.text
        return str(response)

def save_memory() -> None:
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(conversation_history, f, ensure_ascii=False, indent=2)

async def chat_logic(user_input: str, language: str) -> Dict[str, str]:
    global conversation_history, gemini_chat
    
    # Combine data from external sources
    processed_data = "\n".join([f"**{key}**\n{value}" for key, value in data_store.items()])
    
    # Check if search is needed
    check_prompt = (
        f"Conversation History: {json.dumps(conversation_history[-5:], ensure_ascii=False)}\n"
        f"User question: {user_input}\n"
        f"External Data Available: {bool(processed_data)}\n"
        "Kya is sawal ka sahi jawab dene ke liye web search ki zarurat hai? Agar haan toh sirf 'yes' likho, warna 'no'."
    )
    check_response_raw = gemini_chat.send_message(check_prompt)
    check_response = get_gemini_response_text(check_response_raw).strip().lower()

    if "yes" in check_response:
        search_results = await fetch_serpapi_results(user_input, language)
        prompt = (
            f"User asked: {user_input}\n"
            f"Search Results: {search_results.get('organic_results', [])}\n"
            f"External Data: {processed_data if processed_data else 'None'}\n"
            "Summarize the best options in the user's language. "
            "For text, use bullet points or paragraphs with bold headings. "
            "For code, provide a syntax-highlighted block using ```python ... ```."
        )
    else:
        prompt = (
            f"User asked: {user_input}\n"
            f"External Data: {processed_data if processed_data else 'None'}\n"
            "Answer the query based on the user input and external data if available."
        )

    if use_gemini:
        response_raw = gemini_chat.send_message(prompt)
        response = get_gemini_response_text(response_raw)
    else:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ]
        ).choices[0].message.content

    conversation_history.append({"role": "user", "content": user_input})
    conversation_history.append({"role": "assistant", "content": response})
    gemini_history.append({"role": "user", "parts": [{"text": user_input}]})
    gemini_history.append({"role": "model", "parts": [{"text": response}]})
    save_memory()
    return {"response": response}

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/chat', methods=['POST'])
def chat():
    user_input = request.json.get('message')
    language = request.json.get('language', 'hi')
    if not user_input:
        return jsonify({"error": "No message provided"}), 400

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    result = loop.run_until_complete(chat_logic(user_input, language))
    return jsonify(result)

def read_file_content(filename: str) -> str:
    try:
        if filename.endswith(('.xlsx', '.xls')):
            df = pd.read_excel(filename)
            return df.to_string()
        else:
            with open(filename, 'r', encoding='utf-8') as f:
                return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"

@app.route('/read_file', methods=['POST'])
def read_file():
    data = request.json
    filename = data.get('filename')
    if not filename:
        return jsonify({"error": "No filename provided"}), 400
    
    content = read_file_content(filename)
    return jsonify({"content": content})

@app.route('/query_file', methods=['POST'])
def query_file():
    data = request.json
    filename = data.get('filename')
    query = data.get('query')
    
    if not filename or not query:
        return jsonify({"error": "Both filename and query are required"}), 400
    
    content = read_file_content(filename)
    
    if use_gemini:
        prompt = f"File content:\n{content}\n\nUser query: {query}\nPlease answer the query based on the file content."
        response_raw = gemini_chat.send_message(prompt)
        response = get_gemini_response_text(response_raw)
    else:
        response = "Gemini API is not available. Please try again later."
    
    return jsonify({"response": response})

@app.route('/read_excel', methods=['POST'])
def read_excel():
    data = request.json
    filename = data.get('filename')
    sheet_name = data.get('sheet_name', 0)
    
    if not filename:
        return jsonify({"error": "No filename provided"}), 400
    
    try:
        if not filename.endswith(('.xlsx', '.xls')):
            return jsonify({"error": "File is not an Excel file"}), 400
            
        df = pd.read_excel(filename, sheet_name=sheet_name)
        result = {
            "columns": df.columns.tolist(),
            "data": df.values.tolist(),
            "shape": df.shape
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/query_excel', methods=['POST'])
def query_excel():
    data = request.json
    filename = data.get('filename')
    query = data.get('query')
    sheet_name = data.get('sheet_name', 0)
    
    if not filename or not query:
        return jsonify({"error": "Both filename and query are required"}), 400
    
    try:
        if not filename.endswith(('.xlsx', '.xls')):
            return jsonify({"error": "File is not an Excel file"}), 400
            
        df = pd.read_excel(filename, sheet_name=sheet_name)
        excel_info = f"""
        Excel File Information:
        - Number of rows: {df.shape[0]}
        - Number of columns: {df.shape[1]}
        - Column names: {', '.join(df.columns)}
        - First few rows of data:
        {df.head().to_string()}
        """
        prompt = f"{excel_info}\n\nUser query: {query}\nPlease analyze this Excel data and answer the query."
        response_raw = gemini_chat.send_message(prompt)
        response = get_gemini_response_text(response_raw)
    except Exception as e:
        return jsonify({"error": str(e)}), 400
    
    return jsonify({"response": response})

@app.route('/file_reader')
def file_reader():
    return render_template('file_reader.html')

def read_uploaded_file(file: Any) -> str:
    try:
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            file.save(temp_file.name)
            if file.filename.endswith(('.xlsx', '.xls')):
                df = pd.read_excel(temp_file.name)
                return df.to_string()
            elif file.filename.endswith('.csv'):
                df = pd.read_csv(temp_file.name)
                return df.to_string()
            elif file.filename.endswith('.json'):
                with open(temp_file.name, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return json.dumps(data, indent=2, ensure_ascii=False)
            else:
                with open(temp_file.name, 'r', encoding='utf-8') as f:
                    return f.read()
    except Exception as e:
        return f"Error reading file: {str(e)}"
    finally:
        try:
            os.unlink(temp_file.name)
        except:
            pass

@app.route('/upload_file', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    content = read_uploaded_file(file)
    return jsonify({"content": content})

@app.route('/query_uploaded_file', methods=['POST'])
def query_uploaded_file():
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    
    file = request.files['file']
    query = request.form.get('query')
    
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400
    
    if not query:
        return jsonify({"error": "No query provided"}), 400
    
    content = read_uploaded_file(file)
    
    if use_gemini:
        prompt = f"File content:\n{content}\n\nUser query: {query}\nPlease answer the query based on the file content."
        response_raw = gemini_chat.send_message(prompt)
        response = get_gemini_response_text(response_raw)
    else:
        response = "Gemini API is not available. Please try again later."
    
    return jsonify({"response": response})

if __name__ == '__main__':
    app.run(debug=True)