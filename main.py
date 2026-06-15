"""Orchestration entrypoint.

    python main.py --invoice_path=data/invoices/invoice_1001.txt   # process one invoice
    python main.py --invoice_path=... --server=http://host:port    # against a running backend
    python main.py serve                                           # run the backend (also serves the frontend)

Everything routes through the one HTTP API: `serve` exposes it over a socket;
processing calls it, in-process by default or over the network with --server.
"""

import argparse
import asyncio
import socket


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex((host, port)) == 0


def _find_open_port(host: str, port: int, attempts: int = 20) -> int:
    for candidate in range(port, port + attempts):
        if not _port_in_use(host, candidate):
            return candidate
    raise SystemExit(f"no open port found in range {port}-{port + attempts - 1}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="main.py", description="Galatiq invoice pipeline")
    parser.add_argument("--invoice_path", help="path to an invoice document to process")
    parser.add_argument("--approve", type=int, metavar="ID",
                        help="clear a held invoice through human review (pays it)")
    parser.add_argument("--server", default=None, help="base URL of a running backend (default: in-process)")
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="run the backend API + frontend")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8377)

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn

        port = _find_open_port(args.host, args.port)
        if port != args.port:
            print(f"port {args.port} in use; serving on {port} instead")
        uvicorn.run("backend.app:app", host=args.host, port=port)
    elif args.approve is not None:
        from cli.client import approve

        asyncio.run(approve(args.approve, args.server))
    elif args.invoice_path:
        from cli.client import process

        asyncio.run(process(args.invoice_path, args.server))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
