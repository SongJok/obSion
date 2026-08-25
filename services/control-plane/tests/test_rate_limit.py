from obsion.capabilities.rate_limit import InMemoryFixedWindowRateLimiter


async def test_in_memory_capability_rate_limit_is_scoped_and_bounded() -> None:
    limiter = InMemoryFixedWindowRateLimiter(default_limit=2)

    assert await limiter.allow("org:user:capability:connector") is True
    assert await limiter.allow("org:user:capability:connector") is True
    assert await limiter.allow("org:user:capability:connector") is False
    assert await limiter.allow("org:other-user:capability:connector") is True


async def test_connector_limit_can_be_stricter_than_default() -> None:
    limiter = InMemoryFixedWindowRateLimiter(default_limit=100)

    assert await limiter.allow("key", 1) is True
    assert await limiter.allow("key", 1) is False
