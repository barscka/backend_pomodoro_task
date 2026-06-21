from collections.abc import Mapping
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured


POSTGRES_ENVIRONMENTS = {"development", "production"}
SUPPORTED_ENVIRONMENTS = POSTGRES_ENVIRONMENTS | {"test"}
REQUIRED_POSTGRES_VARIABLES = (
    "DB_NAME",
    "DB_USER",
    "DB_PASS",
    "DB_HOST",
    "DB_PORT",
)


def build_database_config(
    *,
    app_env: str,
    environ: Mapping[str, str],
    base_dir: Path,
) -> dict[str, object]:
    """Build a safe Django database configuration for one explicit environment."""
    if app_env not in SUPPORTED_ENVIRONMENTS:
        raise ImproperlyConfigured(
            "APP_ENV deve ser development, production ou test."
        )

    if app_env == "test":
        return _build_test_database_config(environ=environ, base_dir=base_dir)

    return _build_postgres_config(environ=environ)


def _build_postgres_config(*, environ: Mapping[str, str]) -> dict[str, object]:
    missing = [name for name in REQUIRED_POSTGRES_VARIABLES if not environ.get(name)]
    if missing:
        raise ImproperlyConfigured(
            "Variáveis obrigatórias de PostgreSQL ausentes: " + ", ".join(missing)
        )

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": environ["DB_NAME"],
        "USER": environ["DB_USER"],
        "PASSWORD": environ["DB_PASS"],
        "HOST": environ["DB_HOST"],
        "PORT": environ["DB_PORT"],
    }


def _build_test_database_config(
    *,
    environ: Mapping[str, str],
    base_dir: Path,
) -> dict[str, object]:
    database_url = environ.get(
        "TEST_DATABASE_URL",
        "sqlite:///tests/.tmp/app_test.sqlite",
    )
    sqlite_prefix = "sqlite:///"
    if not database_url.startswith(sqlite_prefix):
        raise ImproperlyConfigured(
            "TEST_DATABASE_URL deve apontar para um banco SQLite isolado."
        )

    relative_name = database_url.removeprefix(sqlite_prefix)
    if not relative_name or relative_name.startswith("/"):
        raise ImproperlyConfigured(
            "TEST_DATABASE_URL deve usar um caminho SQLite relativo ao projeto."
        )

    database_path = (base_dir / relative_name).resolve()
    controlled_directory = (base_dir / "tests/.tmp").resolve()
    if not database_path.is_relative_to(controlled_directory):
        raise ImproperlyConfigured(
            "TEST_DATABASE_URL deve permanecer dentro de tests/.tmp."
        )

    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": database_path,
    }


def build_legacy_sqlite_config(
    *,
    environ: Mapping[str, str],
    base_dir: Path,
) -> dict[str, object]:
    """Point migration commands to an explicit SQLite copy, never the source file."""
    configured_path = environ.get("LEGACY_SQLITE_PATH")
    if not configured_path:
        raise ImproperlyConfigured(
            "LEGACY_SQLITE_PATH deve apontar explicitamente para uma cópia do SQLite."
        )

    database_path = Path(configured_path).expanduser().resolve()
    source_path = (base_dir / "db.sqlite3").resolve()
    if database_path == source_path:
        raise ImproperlyConfigured(
            "LEGACY_SQLITE_PATH deve apontar para uma cópia, nunca para db.sqlite3."
        )

    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": database_path,
    }
