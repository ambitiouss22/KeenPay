"""Development-only routes (disabled in production)."""

from fastapi import APIRouter

from config.settings import get_settings
from core.jwt import JWTManager
from dependencies.auth import CurrentUser

router = APIRouter(prefix="/api/v1/dev", tags=["dev"])


@router.get("/token")
async def dev_token(user_id: str = "user_dev_shopper") -> dict:
    """Issue a dev JWT without password (local only)."""
    settings = get_settings()
    if settings.is_production:
        return {"error": "disabled in production"}

    jwt_mgr = JWTManager(settings)
    token = jwt_mgr.create_access_token(
        user_id=user_id,
        merchant_id="merchant_keen",
        role=_role_for_user(user_id),
    )
    return {"access_token": token, "token_type": "bearer"}


@router.get("/whoami")
async def dev_whoami(principal: CurrentUser) -> dict:
    return {
        "user_id": principal.user_id,
        "merchant_id": principal.merchant_id,
        "role": principal.role,
        "auth_method": principal.auth_method,
    }


@router.post("/razorpay/simulate-payment")
async def simulate_payment(payment_link_id: str) -> dict:
    """Simulate Razorpay payment in dev/mock mode."""
    settings = get_settings()
    if settings.is_production:
        return {"error": "disabled in production"}
    from services.razorpay_mock import RazorpayMockService

    return await RazorpayMockService().simulate_payment(payment_link_id)


def _role_for_user(user_id: str) -> str:
    mapping = {
        "user_dev_shopper": "shopper",
        "user_dev_support": "support_agent",
        "user_dev_manager": "manager",
        "user_dev_admin": "admin",
    }
    return mapping.get(user_id, "shopper")
