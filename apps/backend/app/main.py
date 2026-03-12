from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import os

app = FastAPI(
    title="Multimodal RAG API",
    description="Backend API for Document Upload, Processing, and RAG Querying",
    version="1.0.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Welcome to Multimodal RAG API", "status": "running"}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "provider": os.getenv("MODEL_PROVIDER", "ollama")}

# 這裡未來會 include 其他 router (如 /api/v1/chat, /api/v1/upload)
# app.include_router(chat_router, prefix="/api/v1")
