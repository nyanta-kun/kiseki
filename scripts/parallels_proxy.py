"""Parallels向けリバースプロキシ

Docker Desktop for Mac の制限により、Parallels Windows から
Dockerコンテナに直接アクセスできない問題を回避する。

MacのParallels用NIC (10.211.55.2:8000) でリッスンし、
Docker (127.0.0.1:8000) へ転送する。

使い方:
  python3 scripts/parallels_proxy.py
  python3 scripts/parallels_proxy.py --host 10.211.55.2 --port 8000 --target-port 8000
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("parallels_proxy")

PARALLELS_HOST = "0.0.0.0"
PROXY_PORT = 8000
TARGET_HOST = "127.0.0.1"
TARGET_PORT = 8001  # Docker は 127.0.0.1:8001 にマップ


def forward(src: socket.socket, dst: socket.socket) -> None:
    """2ソケット間でデータを転送する。"""
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        try:
            src.close()
        except OSError:
            pass
        try:
            dst.close()
        except OSError:
            pass


def handle_client(client: socket.socket) -> None:
    """クライアント接続をバックエンドへ中継する。"""
    try:
        backend = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        backend.connect((TARGET_HOST, TARGET_PORT))
        threading.Thread(target=forward, args=(client, backend), daemon=True).start()
        threading.Thread(target=forward, args=(backend, client), daemon=True).start()
    except OSError as e:
        logger.error(f"Backend connection failed: {e}")
        client.close()


def run_proxy(host: str, port: int, target_port: int) -> None:
    """プロキシサーバーを起動する。"""
    global TARGET_PORT
    TARGET_PORT = target_port

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(50)
    logger.info(f"Proxy listening on {host}:{port} → {TARGET_HOST}:{target_port}")
    logger.info("Windows側から http://10.211.55.2:8000 でアクセス可能")

    try:
        while True:
            client, addr = server.accept()
            threading.Thread(target=handle_client, args=(client,), daemon=True).start()
    except KeyboardInterrupt:
        logger.info("Proxy stopped")
    finally:
        server.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parallels向けリバースプロキシ")
    parser.add_argument("--host", default=PARALLELS_HOST)
    parser.add_argument("--port", type=int, default=PROXY_PORT)
    parser.add_argument("--target-port", type=int, default=TARGET_PORT)
    args = parser.parse_args()
    run_proxy(args.host, args.port, args.target_port)
