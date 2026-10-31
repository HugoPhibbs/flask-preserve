import gzip
import brotli
from compression import zstd

from abc import ABC, abstractmethod
import functools
import redis
from flask import Response, make_response, request

ENCODERS = {
    "zstd": lambda data: zstd.compress(data, level=3),
    "br": lambda data: brotli.compress(data, quality=4),
    "gzip": lambda data: gzip.compress(data, compresslevel=6),
}


class Preserve(ABC):
    def __init__(self, cache_prefix=None):
        self.cache_prefix = cache_prefix

    @abstractmethod
    def get(self, key: str) -> bytes | str | None:
        pass

    @abstractmethod
    def set(self, key: str, status_code: int, body, timeout: int = 0) -> None:
        pass

    def preserve_response(
        self, compress: bool = False, timeout: int = 300, query_string: bool = False
    ):
        """
        Caches the response of a Flask view function in Redis.

        :param compress: Whether to compress the response
        :param timeout: The time in seconds for which the cached response will be valid.
        :param query_string: If True, the query string will be included in the cache key.
        """

        def decorator(view):
            @functools.wraps(view)
            def wrapper(*args, **kwargs):
                encoding = "identity"
                if compress:
                    encoding = (
                        request.accept_encodings.best_match(list(ENCODERS))
                        or "identity"
                    )

                key = self.cache_key(
                    request.full_path if query_string else request.path, encoding
                )
                cached_value = self.get(key)

                if cached_value is not None:
                    status_code, mimetype, body = self.parse_cache_value(cached_value)
                    return self.preserved_response_obj(
                        body, status_code, encoding, hit=True, mimetype=mimetype, timeout=timeout
                    )

                # Cache miss
                response = make_response(view(*args, **kwargs))
                status_code = response.status_code
                if not response.is_sequence or status_code >= 400:  # Don't cache files or client errors
                    return response

                body = response.get_data()
                if encoding != "identity":
                    body = ENCODERS[encoding](body)

                self.set(key, status_code, body, timeout=timeout)
                mimetype = response.mimetype

                return self.preserved_response_obj(
                    body, status_code, encoding, hit=False, mimetype=mimetype, timeout=timeout
                )

            return wrapper

        return decorator

    def preserved_response_obj(self, body, status, encoding, hit, mimetype, timeout):
        response = Response(
            body, status=status, mimetype=mimetype
        )
        if encoding != "identity":
            response.headers["Content-Encoding"] = encoding
        response.headers["Vary"] = "Accept-Encoding"
        response.headers["App-Cache"] = "HIT" if hit else "MISS"
        response.headers["Cache-Control"] = (
            f"public, max-age={timeout}, s-maxage={timeout}"
        )
        return response

    def cache_key(self, path, encoding) -> str:
        """
        Generates a cache key based on the encoding, path, and query string.

        :param encoding: The encoding used for the response.
        :param path: The request path.
        :return: A string representing the cache key.
        """
        encoding_abrev = ""
        if encoding == "identity":
            encoding_abrev = "ID"
        elif encoding == "gzip":
            encoding_abrev = "GZ"
        elif encoding == "zstd":
            encoding_abrev = "ZS"
        elif encoding == "br":
            encoding_abrev = "BR"
        else:
            raise ValueError(f"Unsupported encoding: {encoding}")
        return f"{self.cache_prefix}:response:{encoding_abrev}:{path}"

    def cache_value(self, response: Response) -> bytes:
        """
        Generates a cache value based on the status code and body.

        :param response: The response to cache.
        :return: A bytes object representing the cache value.
        """
        assert not response.is_sequence, "Cannot cache file responses"
        
        status_code = response.status_code
        mimetype = response.mimetype

        body = response.get_data()

        return str(f"{status_code}:{mimetype}").encode() + b"\n" + body

    def parse_cache_value(self, value) -> tuple[int, str, bytes]:
        """
        Parses a cache value into its components: status code and body.

        :param value: The cache value to parse.
        :return: A tuple containing the status code and body.
        """
        if len(value) < 3:
            raise ValueError("Invalid cache value format")

        meta, body = value.split(b"\n", 1)
        status_code, mimetype = meta.decode().split(":", 1)
        status_code = int(status_code)

        return status_code, mimetype, body


class RedisPreserve(Preserve):

    def __init__(self, redis_client: redis.Redis, cache_prefix=None):
        super().__init__(cache_prefix)
        self.redis_client = redis_client

    def get(self, key):
        try:
            return self.redis_client.get(key)
        except redis.RedisError:
            # Ignore cache errors
            return None

    def set(self, key, status_code, body, timeout=0):
        if timeout <= 0:
            timeout = None
        value = self.cache_value(status_code, body)
        try:
            self.redis_client.set(key, value, ex=timeout)
        except redis.RedisError:
            # Ignore cache errors
            pass


class LocalPreserve(Preserve):
    def __init__(self, cache_prefix=None):
        super().__init__(cache_prefix)
        self.cache = {}

    def get(self, key):
        return self.cache.get(key)

    def set(self, key, status_code, body, timeout=0):
        value = self.cache_value(status_code, body)
        self.cache[key] = value
