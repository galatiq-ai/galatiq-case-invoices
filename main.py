"""Orchestration entrypoint.

    python main.py            # say hello via the in-process API client (no server)
    python main.py hello      # same; --server URL to hit a running backend
    python main.py serve      # run the backend (also serves the frontend)

Everything routes through the one HTTP API: `serve` exposes it over a socket;
`hello` calls it, in-process by default or over the network with --server.
"""

import argparse
import asyncio


def main() -> None:
    parser = argparse.ArgumentParser(prog="main.py", description="Galatiq invoice pipeline (scaffold)")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the backend API + frontend")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8377)

    hello = sub.add_parser("hello", help="call the hello API")
    hello.add_argument("--server", default=None, help="base URL of a running backend (default: in-process)")

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn

        uvicorn.run("backend.app:app", host=args.host, port=args.port)
    else:
        from cli.client import hello as run_hello

        asyncio.run(run_hello(getattr(args, "server", None)))


if __name__ == "__main__":
    main()
