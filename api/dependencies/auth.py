"""FastAPI auth dependencies."""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from core.exceptions import KeenPayError
from core.rbac import Permission, require_permission
from services.auth import AuthenticatedPrincipal, AuthService

_bearer = HTTPBearer(auto_error=False)


def _publish(request: Request, principal: AuthenticatedPrincipal) -> None:
    """Mirror the verified principal onto request.state.

    Middleware already set these from the token; writing them again keeps the
    two paths consistent when a request authenticates by API key, which the
    middleware does not decode.
    """
    request.state.principal = principal
    request.state.tenant_id = principal.tenant_id
    request.state.merchant_id = principal.merchant_id
    request.state.user_id = principal.user_id
    request.state.role = principal.role


def get_auth_service() -> AuthService:
    return AuthService()


async def get_current_principal(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    auth_service: AuthService = Depends(get_auth_service),
) -> AuthenticatedPrincipal:
    if credentials and credentials.scheme.lower() == "bearer":
        try:
            principal = auth_service.verify_access_token(credentials.credentials)
            _publish(request, principal)
            return principal
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": "UNAUTHORIZED", "message": str(exc)}},
            ) from exc

    if x_api_key:
        try:
            principal = await auth_service.authenticate_api_key(x_api_key)
            _publish(request, principal)
            return principal
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": "UNAUTHORIZED", "message": str(exc)}},
            ) from exc

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"error": {"code": "UNAUTHORIZED", "message": "Missing credentials"}},
    )


def require_roles(*roles: str):
    async def _checker(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    ) -> AuthenticatedPrincipal:
        if principal.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "FORBIDDEN",
                        "message": f"Role '{principal.role}' not permitted",
                    }
                },
            )
        return principal

    return _checker


def require_perm(permission: Permission):
    async def _checker(
        principal: Annotated[AuthenticatedPrincipal, Depends(get_current_principal)],
    ) -> AuthenticatedPrincipal:
        try:
            # Scopes are passed through, so a scoped credential is checked
            # against the intersection of its role and its scopes. Passing
            # ``None`` for an unscoped principal keeps every existing caller
            # behaving exactly as before.
            require_permission(principal.role, permission, principal.scopes)
        except KeenPayError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": "FORBIDDEN", "message": str(exc)}},
            ) from exc
        return principal

    return _checker


CurrentUser = Annotated[AuthenticatedPrincipal, Depends(get_current_principal)]
