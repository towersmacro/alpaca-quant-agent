"""
Retry handler for async HTTP operations with different retry strategies.

Provides exponential backoff with jitter for:
- Network errors (timeouts, connection refused)
- HTTP status codes (429 rate limits, 5xx server errors)
"""

import asyncio
import random
import logging
from typing import Callable, TypeVar, Optional, Tuple
from functools import wraps
import httpx

logger = logging.getLogger("quant_agent_trading")

T = TypeVar('T')


class RetryStrategy:
    """Retry configuration for different operation types."""
    
    ORDER_SUBMIT = {
        'max_attempts': 3,
        'base_delay': 0.5,
        'max_delay': 5.0,
        'retry_on': (429, 500, 502, 503, 504),
    }
    
    READ_OPERATION = {
        'max_attempts': 5,
        'base_delay': 0.3,
        'max_delay': 10.0,
        'retry_on': (429, 500, 502, 503, 504),
    }
    
    PRICE_FETCH = {
        'max_attempts': 3,
        'base_delay': 0.2,
        'max_delay': 3.0,
        'retry_on': (429, 500, 502, 503, 504),
    }


def _should_retry(status_code: Optional[int], exception: Optional[Exception], retry_on: Tuple[int, ...]) -> bool:
    if status_code:
        return status_code in retry_on
    if exception:
        if isinstance(exception, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
            return True
        if isinstance(exception, httpx.HTTPStatusError):
            return exception.response.status_code in retry_on
    return False


def _calculate_backoff(attempt: int, base_delay: float, max_delay: float) -> float:
    delay = base_delay * (2 ** attempt)
    delay = min(delay, max_delay)
    jitter = random.uniform(0, delay * 0.25)
    return delay + jitter


async def retry_async(
    func: Callable[..., T],
    strategy: dict,
    *args,
    **kwargs
) -> T:
    max_attempts = strategy['max_attempts']
    base_delay = strategy['base_delay']
    max_delay = strategy['max_delay']
    retry_on = strategy['retry_on']
    
    last_exception = None
    last_status = None
    
    for attempt in range(max_attempts):
        try:
            result = await func(*args, **kwargs)
            return result
        except httpx.HTTPStatusError as e:
            last_exception = e
            last_status = e.response.status_code
            
            if last_status in (401, 403, 400):
                logger.error(f"Non-retryable error {last_status}: {e}")
                raise
            
            if not _should_retry(last_status, e, retry_on):
                raise
            
            if attempt < max_attempts - 1:
                backoff = _calculate_backoff(attempt, base_delay, max_delay)
                logger.warning(f"HTTP {last_status} on attempt {attempt + 1}/{max_attempts}, retrying in {backoff:.2f}s")
                await asyncio.sleep(backoff)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as e:
            last_exception = e
            if attempt < max_attempts - 1:
                backoff = _calculate_backoff(attempt, base_delay, max_delay)
                logger.warning(f"Network error on attempt {attempt + 1}/{max_attempts}: {e}, retrying in {backoff:.2f}s")
                await asyncio.sleep(backoff)
        except Exception as e:
            logger.error(f"Non-retryable exception: {e}")
            raise
    
    logger.error(f"All {max_attempts} attempts failed. Last error: {last_exception}")
    raise last_exception


def with_retry(strategy: dict):
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            return await retry_async(func, strategy, *args, **kwargs)
        return wrapper
    return decorator


async def retry_http_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    strategy: dict,
    **request_kwargs
) -> httpx.Response:
    async def _make_request():
        response = await client.request(method, url, **request_kwargs)
        response.raise_for_status()
        return response
    
    return await retry_async(_make_request, strategy)
