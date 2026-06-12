import os
from google import genai
from dotenv import load_dotenv

env_path = "/home/johnlai/projects/.env"
load_dotenv(env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

print("Testing gemini-2.5-flash-lite call...")
try:
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents="Hello, respond with a short sentence in Traditional Chinese."
    )
    print("Response:")
    print(response.text)
except Exception as e:
    print(f"Error calling gemini-2.5-flash-lite: {e}")
