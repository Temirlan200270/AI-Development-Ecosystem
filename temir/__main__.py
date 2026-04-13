"""Поддержка `python -m temir ...` (то же, что CLI `temir`)."""

from temir.main import app

if __name__ == "__main__":
    app()
