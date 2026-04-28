"""Command-line entry points.

  fatsecret-mcp serve          # run the MCP stdio server
  fatsecret-mcp auth           # interactive 3-legged OAuth 1.0a setup
  fatsecret-mcp whoami         # sanity-check: print the authenticated profile
  fatsecret-mcp config-path    # print where config lives
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from .client import Client
from .config import Config, config_path
from .oauth import Consumer


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import build_server
    server = build_server()

    if args.transport == "stdio":
        server.run(transport="stdio")
        return 0

    # Hosted transports (sse / streamable-http).
    server.settings.host = args.host
    server.settings.port = args.port
    # FastMCP's default DNS rebinding protection only allows localhost hosts.
    # Disable for hosted deploys so external hostnames reach the endpoint.
    server.settings.transport_security.enable_dns_rebinding_protection = False

    auth_token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if not auth_token:
        # No bearer token configured: run unauthenticated (URL-as-secret posture).
        server.run(transport=args.transport)
        return 0

    # Bearer-auth mode: wrap FastMCP's ASGI app in a Starlette app whose
    # middleware list is passed at construction time. (Calling
    # `streamable_http_app().add_middleware()` after the fact doesn't take —
    # FastMCP finalizes its own middleware stack before that runs.)
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Mount
    import uvicorn

    class BearerAuthMiddleware(BaseHTTPMiddleware):
        def __init__(self, app, token: str):
            super().__init__(app)
            self._token = token

        async def dispatch(self, request, call_next):
            # Allow CORS preflight unauthenticated; everything else requires bearer.
            if request.method == "OPTIONS":
                return await call_next(request)
            if request.headers.get("authorization") != f"Bearer {self._token}":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

    if args.transport == "sse":
        mcp_app = server.sse_app()
    else:  # streamable-http
        mcp_app = server.streamable_http_app()

    app = Starlette(
        middleware=[Middleware(BearerAuthMiddleware, token=auth_token)],
        routes=[Mount("/", app=mcp_app)],
        lifespan=lambda _outer: mcp_app.router.lifespan_context(mcp_app),
    )

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def cmd_auth(args: argparse.Namespace) -> int:
    print("FatSecret 3-legged OAuth setup.\n")

    consumer_key = input("Consumer key: ").strip()
    consumer_secret = getpass.getpass("Consumer secret (hidden): ").strip()
    if not (consumer_key and consumer_secret):
        print("both are required", file=sys.stderr)
        return 1

    client = Client(consumer=Consumer(key=consumer_key, secret=consumer_secret))

    print("\n1/3  requesting request token…")
    req_token = client.request_token(callback_uri="oob")
    url = client.authorize_url(req_token)
    print(f"\n2/3  open this URL in a browser signed in to your FatSecret USER account")
    print(f"     (not the platform developer account):\n\n     {url}\n")
    print("     Click Allow, then FS will show you a numeric verifier PIN.")
    verifier = input("\n     Paste the PIN here: ").strip()
    if not verifier:
        print("verifier required", file=sys.stderr)
        return 1

    print("\n3/3  exchanging verifier for access token…")
    user_token = client.access_token(req_token, verifier)

    cfg = Config(consumer=client.consumer, user_token=user_token)
    path = cfg.save()
    print(f"\nSaved to {path} (mode 0600). You can now run `fatsecret-mcp serve`.")
    return 0


def cmd_whoami(args: argparse.Namespace) -> int:
    cfg = Config.load()
    if cfg.user_token is None:
        print("no user token — run `fatsecret-mcp auth` first", file=sys.stderr)
        return 1
    client = Client(consumer=cfg.consumer, token=cfg.user_token)
    res = client.call("profile.get")
    print(json.dumps(res.get("profile", {}), indent=2))
    return 0


def cmd_config_path(args: argparse.Namespace) -> int:
    print(config_path())
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fatsecret-mcp", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the MCP server (stdio by default; sse / streamable-http for hosted deploys)")
    serve.add_argument("--transport", choices=["stdio", "sse", "streamable-http"], default="stdio",
                       help="Transport. stdio (default) for local MCP clients; sse for hosted deploys; streamable-http for the newer MCP HTTP transport.")
    serve.add_argument("--host", default="0.0.0.0", help="Bind host for sse/streamable-http (default: 0.0.0.0)")
    serve.add_argument("--port", type=int, default=8000, help="Bind port for sse/streamable-http (default: 8000)")
    serve.set_defaults(func=cmd_serve)
    sub.add_parser("auth", help="interactive 3-legged OAuth 1.0a setup").set_defaults(func=cmd_auth)
    sub.add_parser("whoami", help="print the authenticated profile").set_defaults(func=cmd_whoami)
    sub.add_parser("config-path", help="print the config file path").set_defaults(func=cmd_config_path)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
