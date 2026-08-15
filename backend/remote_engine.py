import urllib.parse
import os
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

REMOTE_DB_USER = os.getenv("REMOTE_DB_USER")
REMOTE_DB_PASSWORD = os.getenv("REMOTE_DB_PASSWORD")
REMOTE_DB_SERVER = os.getenv("REMOTE_DB_SERVER")
REMOTE_DB_PORT = os.getenv("REMOTE_DB_PORT", "1433")

_safe_password = urllib.parse.quote_plus(REMOTE_DB_PASSWORD) if REMOTE_DB_PASSWORD else ""


def _make_remote_url(db_name):
    return (
        f"mssql+pymssql://{REMOTE_DB_USER}:{_safe_password}"
        f"@{REMOTE_DB_SERVER}:{REMOTE_DB_PORT}/{db_name}"
    )


_remote_engine_cache = {}


def get_remote_engine(db_name):
    if db_name not in _remote_engine_cache:
        url = _make_remote_url(db_name)
        _remote_engine_cache[db_name] = create_engine(
            url,
            connect_args={
                "login_timeout": 15,
                "tds_version": "7.3",
                "encryption": "off",
            },
        )
    return _remote_engine_cache[db_name]


def get_remote_session(db_name):
    engine = get_remote_engine(db_name)
    Session = sessionmaker(bind=engine)
    return Session()
