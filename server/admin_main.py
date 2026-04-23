from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Import admin API routes
from admin_api import router as admin_router

app = FastAPI(title="FoodAware Admin Manager")

# Include admin API FIRST before static mounting
app.include_router(admin_router)

# Mount static UI directory AFTER API routes
ADMIN_UI_DIR = os.path.join(os.path.dirname(__file__), "admin_ui")
if not os.path.exists(ADMIN_UI_DIR):
    os.makedirs(ADMIN_UI_DIR)
# html=True enables serving index.html automatically on directory requests
app.mount("/admin", StaticFiles(directory=ADMIN_UI_DIR, html=True), name="admin")

# Mount marks and uploads (Admin needs access to these)
MARKS_DIR = "/home/johnlai/projects/FoodScanIoT/Clients/Mobile_App/Resource/食品標章"
if os.path.exists(MARKS_DIR):
    app.mount("/marks", StaticFiles(directory=MARKS_DIR), name="marks")

UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "uploads")
if not os.path.exists(UPLOADS_DIR):
    os.makedirs(UPLOADS_DIR)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)

@app.get("/")
async def root():
    # Redirect root to /admin/ index
    return RedirectResponse(url="/admin/")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3003)
