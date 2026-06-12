import os
import sys
from dotenv import load_dotenv
from google import genai

env_path = "/home/johnlai/projects/.env"
load_dotenv(env_path)

api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)
try:
    print("Sending request to gemma-4-31b-it...")
    response = client.models.generate_content(
        model='gemma-4-31b-it',
        contents='Hello, please respond in one word.'
    )
    print("Gemma Response:", response.text)
except Exception as e:
    print("Gemma Error:", e)
