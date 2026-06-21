from pathlib import Path
from unittest import TestCase

from django.core.exceptions import ImproperlyConfigured

from config.settings.database import (
    build_database_config,
    build_legacy_sqlite_config,
)


class BuildDatabaseConfigTests(TestCase):
    def setUp(self):
        self.base_dir = Path("/tmp/pomodoro-test-project")
        self.postgres_environment = {
            "DB_NAME": "pomodoro_task_dev",
            "DB_USER": "pomodoro_task_dev_user",
            "DB_PASS": "local-secret",
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5432",
        }

    def test_builds_postgresql_configuration_for_development(self):
        database = build_database_config(
            app_env="development",
            environ=self.postgres_environment,
            base_dir=self.base_dir,
        )

        self.assertEqual(database["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(database["NAME"], "pomodoro_task_dev")
        self.assertEqual(database["USER"], "pomodoro_task_dev_user")
        self.assertEqual(database["PASSWORD"], "local-secret")
        self.assertEqual(database["HOST"], "127.0.0.1")
        self.assertEqual(database["PORT"], "5432")

    def test_builds_postgresql_configuration_for_production(self):
        environment = {
            **self.postgres_environment,
            "DB_NAME": "pomodoro_task_prod",
            "DB_USER": "pomodoro_task_user",
            "DB_HOST": "postgres",
        }

        database = build_database_config(
            app_env="production",
            environ=environment,
            base_dir=self.base_dir,
        )

        self.assertEqual(database["ENGINE"], "django.db.backends.postgresql")
        self.assertEqual(database["NAME"], "pomodoro_task_prod")
        self.assertEqual(database["USER"], "pomodoro_task_user")
        self.assertEqual(database["HOST"], "postgres")

    def test_rejects_missing_postgresql_variables(self):
        environment = {**self.postgres_environment, "DB_PASS": ""}

        with self.assertRaisesRegex(ImproperlyConfigured, "DB_PASS"):
            build_database_config(
                app_env="development",
                environ=environment,
                base_dir=self.base_dir,
            )

    def test_builds_isolated_sqlite_configuration_for_tests(self):
        environment = {
            **self.postgres_environment,
            "TEST_DATABASE_URL": "sqlite:///tests/.tmp/app_test.sqlite",
        }

        database = build_database_config(
            app_env="test",
            environ=environment,
            base_dir=self.base_dir,
        )

        self.assertEqual(database["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(
            database["NAME"],
            self.base_dir / "tests/.tmp/app_test.sqlite",
        )
        self.assertNotIn("HOST", database)
        self.assertNotIn("USER", database)
        self.assertNotIn("PASSWORD", database)

    def test_rejects_non_sqlite_database_for_tests(self):
        environment = {
            "TEST_DATABASE_URL": (
                "postgresql://pomodoro_task_user:secret@postgres:5432/"
                "pomodoro_task_test"
            ),
        }

        with self.assertRaisesRegex(ImproperlyConfigured, "SQLite"):
            build_database_config(
                app_env="test",
                environ=environment,
                base_dir=self.base_dir,
            )

    def test_rejects_sqlite_test_database_outside_controlled_directory(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "tests/.tmp"):
            build_database_config(
                app_env="test",
                environ={"TEST_DATABASE_URL": "sqlite:///db.sqlite3"},
                base_dir=self.base_dir,
            )

    def test_rejects_unknown_application_environment(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "APP_ENV"):
            build_database_config(
                app_env="staging-typo",
                environ=self.postgres_environment,
                base_dir=self.base_dir,
            )


class BuildLegacySqliteConfigTests(TestCase):
    def setUp(self):
        self.base_dir = Path("/tmp/pomodoro-project")

    def test_requires_explicit_legacy_sqlite_path(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "LEGACY_SQLITE_PATH"):
            build_legacy_sqlite_config(environ={}, base_dir=self.base_dir)

    def test_rejects_the_original_project_database(self):
        with self.assertRaisesRegex(ImproperlyConfigured, "cópia"):
            build_legacy_sqlite_config(
                environ={
                    "LEGACY_SQLITE_PATH": str(self.base_dir / "db.sqlite3"),
                },
                base_dir=self.base_dir,
            )

    def test_builds_configuration_for_an_explicit_copy(self):
        sqlite_copy = Path("/tmp/pomodoro-migration/source.sqlite3")

        database = build_legacy_sqlite_config(
            environ={"LEGACY_SQLITE_PATH": str(sqlite_copy)},
            base_dir=self.base_dir,
        )

        self.assertEqual(database["ENGINE"], "django.db.backends.sqlite3")
        self.assertEqual(database["NAME"], sqlite_copy)
