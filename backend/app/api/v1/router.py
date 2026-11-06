from fastapi import APIRouter

from app.api.v1 import auth, auth_microsoft, health, mailboxes, users, webhooks

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(auth_microsoft.router)
api_router.include_router(users.router)
api_router.include_router(mailboxes.router)
api_router.include_router(webhooks.router)
