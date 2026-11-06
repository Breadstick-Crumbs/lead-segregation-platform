"""Sign in with Microsoft: maps a verified Entra identity onto an existing account.

There is no self-signup. An admin creates the account first; the first Microsoft sign-in links
it by matching the user's UPN (unique within our single tenant), after which the immutable
Entra object ID is what identifies them.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.integrations.microsoft_entra import EntraIdentity
from app.repositories.users import UserRepository
from app.services.auth import AuthService, IssuedTokens

logger = get_logger(__name__)


class SsoSignInError(AppError):
    """Carries a short `reason` the dashboard can explain; details stay in the logs."""

    status_code = 403
    code = "sso_sign_in_failed"

    def __init__(self, reason: str) -> None:
        super().__init__("Microsoft sign-in was not accepted")
        self.reason = reason


class SsoService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._users = UserRepository(session)
        self._auth = AuthService(session, settings)

    async def sign_in(self, identity: EntraIdentity) -> IssuedTokens:
        user = await self._users.get_by_entra_oid(identity.object_id)
        if user is None:
            user = await self._users.get_by_email(identity.username)
            if user is None:
                logger.info("sso_rejected", reason="not_invited", oid=str(identity.object_id))
                raise SsoSignInError("not_invited")
            if user.entra_oid is not None:
                # The address now belongs to a different Microsoft account than the one
                # linked: don't silently re-link (that would hand the account over).
                logger.warning("sso_rejected", reason="link_mismatch", user_id=str(user.id))
                raise SsoSignInError("link_mismatch")
            user.entra_oid = identity.object_id
            logger.info("sso_linked", user_id=str(user.id), oid=str(identity.object_id))

        if not user.is_active:
            logger.info("sso_rejected", reason="inactive", user_id=str(user.id))
            raise SsoSignInError("inactive")

        tokens = await self._auth.start_session(user)
        logger.info("sso_login_succeeded", user_id=str(user.id))
        return tokens
