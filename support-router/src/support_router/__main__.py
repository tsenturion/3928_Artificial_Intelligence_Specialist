"""Позволяет запускать пакет командой python -m support_router."""

from support_router.cli import main

if __name__ == "__main__":
    # При python -m support_router Python выполняет этот файл. SystemExit передаёт
    # возвращённый CLI код оболочке: CI и PowerShell смогут остановиться при ошибке.
    raise SystemExit(main())
