# main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes.items import router as items_router
from app.api.routes.batch import router as batch_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: init clients, db pool, etc.
    yield
    # shutdown: teardown

app = FastAPI(title="Flipr API", version="0.1.0", lifespan=lifespan)

app.include_router(items_router, prefix="/api/v1")
app.include_router(batch_router, prefix="/api/v1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # tighten per-env later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok"}