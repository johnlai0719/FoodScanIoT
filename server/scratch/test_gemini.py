import os
import sys
from dotenv import load_dotenv
from google import genai

env_path = "/home/johnlai/projects/.env"
load_dotenv(env_path)

api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key)
try:
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents='Hello, please respond in one word.'
    )
    print("Gemini Response:", response.text)
except Exception as e:
    print("Gemini Error:", e)
