"""Authentication routes — login, refresh, revoke, profile, API keys."""

from fastapi import APIRouter, Depends, HTTPException, Request, status

from config.settings import get_settings
from core.rbac import Permission
from dependencies.auth import CurrentUser, get_auth_service, require_perm
from schemas.auth import (
    AgentTokenRequest,
    AgentTokenResponse,
    ApiKeyCreateRequest,
    ApiKeyCreateResponse,
    LoginRequest,
    RefreshRequest,
    RevokeRequest,
    TokenResponse,
    UserProfile,
)
from services.auth import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    settings = get_settings()
    try:
        access, refresh, principal = await auth.authenticate_password(
            email=body.email,
            password=body.password,
            merchant_id=body.merchant_id,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": str(exc)}},
        ) from exc

    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_expire_minutes * 60,
        role=principal.role,
        merchant_id=principal.merchant_id,
        user_id=principal.user_id,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    request: Request,
    auth: AuthService = Depends(get_auth_service),
) -> TokenResponse:
    settings = get_settings()
    try:
        access, new_refresh, principal = await auth.refresh_tokens(
            refresh_token=body.refresh_token,
            user_agent=request.headers.get("user-agent"),
            ip_address=request.client.host if request.client else None,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "UNAUTHORIZED", "message": str(exc)}},
        ) from exc

    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        expires_in=settings.jwt_access_expire_minutes * 60,
        role=principal.role,
        merchant_id=principal.merchant_id,
        user_id=principal.user_id,
    )


@router.post("/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(
    body: RevokeRequest,
    auth: AuthService = Depends(get_auth_service),
) -> None:
    await auth.revoke_refresh_token(body.refresh_token)


@router.get("/me", response_model=UserProfile)
async def me(
    principal: CurrentUser,
) -> UserProfile:
    from repositories.users import UserRepository

    if principal.auth_method == "api_key":
        return UserProfile(
            user_id=principal.user_id,
            email=f"apikey@{principal.api_key_id}",
            merchant_id=principal.merchant_id,
            role=principal.role,
            display_name="API Key",
        )

    user = await UserRepository().get_by_id(principal.user_id)
    if not user:
        raise HTTPException(
            status_code=404, detail={"error": {"code": "NOT_FOUND", "message": "User not found"}}
        )
    return UserProfile(
        user_id=user["id"],
        email=user["email"],
        merchant_id=user["merchant_id"],
        role=user["role"],
        display_name=user.get("display_name"),
        last_login_at=user.get("last_login_at"),
    )


@router.post(
    "/api-keys",
    response_model=ApiKeyCreateResponse,
    dependencies=[Depends(require_perm(Permission.API_KEY_MANAGE))],
)
async def create_api_key(
    body: ApiKeyCreateRequest,
    principal: CurrentUser,
    auth: AuthService = Depends(get_auth_service),
) -> ApiKeyCreateResponse:
    raw, record = await auth.create_api_key(
        name=body.name,
        merchant_id=principal.merchant_id,
        role=body.role,
        created_by=principal.user_id,
        scopes=body.scopes,
        expires_in_days=body.expires_in_days,
    )
    return ApiKeyCreateResponse(
        key_id=record["id"],
        api_key=raw,
        name=record["name"],
        prefix=record["key_prefix"],
        role=record["role"],
        expires_at=record.get("expires_at"),
    )


@router.post(
    "/agent-tokens",
    response_model=AgentTokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_perm(Permission.API_KEY_MANAGE))],
)
async def create_agent_token(
    body: AgentTokenRequest,
    principal: CurrentUser,
    auth: AuthService = Depends(get_auth_service),
) -> AgentTokenResponse:
    """Mint a short-lived, audience-restricted credential for an AI agent.

    Gated behind the same permission as API keys, because that is what this
    is: a machine credential, and handing one out is an administrative act.

    The merchant is taken from the caller's own token and the role is fixed at
    ``agent`` server-side. An operator can therefore only ever mint a
    credential for their own merchant, and never one that can approve, refund
    or capture - no combination of request fields reaches those permissions.

    The token is returned once and not stored. It is short-lived by design, so
    the recovery path for a lost one is to mint another rather than to keep a
    long-lived secret somewhere it can be read.
    """
    try:
        token, ttl, granted = auth.issue_agent_token(
            agent_id=body.agent_id,
            merchant_id=principal.merchant_id,
            scopes=body.scopes,
            issued_by=principal.user_id,
            tenant_id=principal.tenant_id,
            ttl_seconds=body.ttl_seconds,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": "INVALID_AGENT_SCOPE", "message": str(exc)}},
        ) from exc

    return AgentTokenResponse(
        access_token=token,
        expires_in=ttl,
        audience=get_settings().control_plane_audience,
        scopes=granted,
        merchant_id=principal.merchant_id,
        agent_id=body.agent_id,
    )
