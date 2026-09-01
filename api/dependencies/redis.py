"""Redis connection."""

from redis import asyncio as aioredis

from config.settings import get_settings

_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


async def check_redis() -> bool:
    if get_settings().use_in_memory_store:
        return True
    try:
        redis = await get_redis()
        return await redis.ping()
    except Exception:
        return False
