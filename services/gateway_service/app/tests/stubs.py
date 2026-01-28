import httpx


class RoutingClient:
    """
    Minimal async client that returns canned responses keyed by (method, path).
    """

    def __init__(self, routes: dict[tuple[str, str], httpx.Response]):
        self.routes = routes

    async def get(self, path: str, headers=None):
        return self._resolve("GET", path)

    async def post(self, path: str, json=None, content=None, files=None, headers=None):
        return self._resolve("POST", path)

    async def put(self, path: str, json=None, content=None, headers=None):
        return self._resolve("PUT", path)

    async def patch(self, path: str, json=None, content=None, headers=None):
        return self._resolve("PATCH", path)

    async def delete(self, path: str, headers=None):
        return self._resolve("DELETE", path)

    def _resolve(self, method: str, path: str) -> httpx.Response:
        key = (method, path)
        if key not in self.routes:
            raise AssertionError(f"Unexpected {method} {path}")
        return self.routes[key]


class StubUser:
    """
    Lightweight stand-in for AuthUser with a token attribute for gateway headers.
    """

    def __init__(
        self,
        *,
        user_id: str = "user-id",
        email: str = "user@example.com",
        role: str = "authenticated",
        token: str = "fake-token",
    ):
        self.user_id = user_id
        self.email = email
        self.role = role
        self.token = token


def make_response(
    status_code: int,
    json_data=None,
    method: str = "GET",
    path: str = "/",
) -> httpx.Response:
    """
    Convenience wrapper to build httpx responses with request context.
    """
    request = httpx.Request(method, f"http://test{path}")
    if json_data is None:
        return httpx.Response(status_code, content=b"", request=request)
    return httpx.Response(status_code, json=json_data, request=request)
