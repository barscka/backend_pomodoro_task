from unittest.mock import patch

from django.db import DatabaseError
from django.test import TestCase


class HealthCheckTests(TestCase):
    def test_health_check_reports_application_and_database_ready(self):
        response = self.client.get("/healthz/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "ok", "database": "ok"},
        )

    @patch("config.health.connection.cursor", side_effect=DatabaseError)
    def test_health_check_returns_unavailable_without_leaking_details(self, _cursor):
        response = self.client.get("/healthz/")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"status": "unavailable", "database": "unavailable"},
        )
