from fastapi import APIRouter

from app.api.v1 import (
    auth,
    auth_microsoft,
    health,
    leads,
    mailboxes,
    notifications,
    reports,
    settings,
    users,
    webhooks,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(auth_microsoft.router)
api_router.include_router(users.router)
api_router.include_router(leads.router)
api_router.include_router(mailboxes.router)
api_router.include_router(notifications.router)
api_router.include_router(reports.router)
api_router.include_router(settings.router)
api_router.include_router(webhooks.router)
