"""
Retry handler for async HTTP operations with different retry strategies.

Provides decorators and context managers for handling:
- Network errors (timeouts, connection refused)
- HTTP status codes (429 rate limits, 5xx server errors)
- Exponential backoff with jitter
- Different retry levels for different operations
"""

import asyncio
import random
import logging
from typing import Callable, TypeVar, Optional, Tuple
from functools import wraps
import httpx

logger = logging.getLogger('live_simulator')

T = TypeVar('T')

# Retry strategies
class RetryStrategy:
    """Retry configuration for different operation types."""
    
    # Order submission: critical, fewer retries to avoid duplicates
    ORDER_SUBMIT = {
        'max_attempts': 3,
        'base_delay': 0.5,
        'max_delay': 5.0,
        'retry_on': (429, 500, 502, 503, 504),
    }
    
    # Read operations: safe to retry many times
    READ_OPERATION = {
        'max_attempts': 5,
        'base_delay': 0.3,
        'max_delay': 10.0,
        'retry_on': (429, 500, 502, 503, 504),
    }
    
    # Price fetching: already has some retry, but can use this for consistency
    PRICE_FETCH = {
        'max_attempts': 3,
        'base_delay': 0.2,
        'max_delay': 3.0,
        'retry_on': (429, 500, 502, 503, 504),
    }


def _should_retry(status_code: Optional[int], exception: Optional[Exception], retry_on: Tuple[int, ...]) -> bool:
    """Determine if we should retry based on status code or exception type."""
    if status_code:
        return status_code in retry_on
    
    if exception:
        # Network errors - always retry
        if isinstance(exception, (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError)):
            return True
        # HTTP errors - check status code if available
        if isinstance(exception, httpx.HTTPStatusError):
            return exception.response.status_code in retry_on
    
    return False


def _calculate_backoff(attempt: int, base_delay: float, max_delay: float) -> float:
    """Calculate exponential backoff with jitter."""
    # Exponential: base_delay * 2^attempt
    delay = base_delay * (2 ** attempt)
    # Cap at max_delay
    delay = min(delay, max_delay)
    # Add jitter: random 0-25% of delay
    jitter = random.uniform(0, delay * 0.25)
    return delay + jitter


async def retry_async(
    func: Callable[..., T],
    strategy: dict,
    *args,
    **kwargs
) -> T:
    """
    Retry an async function with exponential backoff.
    
    Args:
        func: Async function to retry
        strategy: Retry strategy dict (max_attempts, base_delay, max_delay, retry_on)
        *args, **kwargs: Arguments to pass to func
    
    Returns:
        Result from func
    
    Raises:
        Last exception if all retries exhausted
    """
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
            
            # Don't retry on auth errors or bad requests
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
            # Don't retry on unknown exceptions
            logger.error(f"Non-retryable exception: {e}")
            raise
    
    # All retries exhausted
    logger.error(f"All {max_attempts} attempts failed. Last error: {last_exception}")
    raise last_exception


def with_retry(strategy: dict):
    """
    Decorator to add retry logic to async functions.
    
    Usage:
        @with_retry(RetryStrategy.ORDER_SUBMIT)
        async def submit_order(...):
            ...
    """
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
    """
    Retry an HTTP request with exponential backoff.
    
    Args:
        client: httpx.AsyncClient instance
        method: HTTP method ('get', 'post', 'delete', etc.)
        url: Request URL
        strategy: Retry strategy dict
        **request_kwargs: Additional arguments for client.request()
    
    Returns:
        httpx.Response
    
    Raises:
        httpx.HTTPStatusError or httpx.RequestError if all retries fail
    """
    async def _make_request():
        response = await client.request(method, url, **request_kwargs)
        # Raise for status to trigger retry logic
        response.raise_for_status()
        return response
    
    return await retry_async(_make_request, strategy)

