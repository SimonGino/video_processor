"""Thin entry point — delegates to douyu2bilibili.app."""

from douyu2bilibili.app import start_api_server

if __name__ == "__main__":
    start_api_server()
