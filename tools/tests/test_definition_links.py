"""Proof URLs must round-trip to the correct app route and workspace."""
import importlib.util
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit

SCRIPT = (Path(__file__).resolve().parents[2] / "plugins/atlanai-analytics/skills/"
          "analytics-ask/scripts/definition_link.py")
spec = importlib.util.spec_from_file_location("definition_link", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DefinitionLinkTest(unittest.TestCase):
    def test_metric_and_query_routes_round_trip(self):
        for kind, handle, route in (("metric", "metric_example", "/m/metric_example"),
                                    ("sql_query", "sql_query_example", "/queries/sql_query_example")):
            with self.subTest(kind=kind):
                link = module.definition_link("https://app.atlan.com", "example-org", kind,
                                              handle, "workspace_example")
                outer = urlsplit(link)
                self.assertEqual(outer.path, "/example-org/apps/metric")
                params = parse_qs(outer.query)
                self.assertEqual(params["ws"], ["workspace_example"])
                inner = urlsplit(params["path"][0])
                self.assertEqual(inner.path, route)
                self.assertEqual(parse_qs(inner.query), {"workspace": ["workspace_example"]})
                self.assertNotIn("?open=", link)

    def test_verified_environment_is_preserved(self):
        link = module.definition_link("https://dashboard.example.com/", "example-org", "metric",
                                      "metric_example", "workspace_other")
        self.assertTrue(link.startswith("https://dashboard.example.com/example-org/"))
        self.assertEqual(parse_qs(urlsplit(link).query)["ws"], ["workspace_other"])

    def test_rejects_non_origin_and_credential_inputs(self):
        for origin in ("http://app.atlan.com", "https://user:password@example.com",
                       "https://example.com/path", "https://example.com?query=1",
                       "https://example.com#fragment", "https://example.com\\evil",
                       "https://exa mple.com"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                module.definition_link(origin, "example-org", "metric", "metric_example",
                                       "workspace_example")

    def test_rejects_wrong_kind_and_injected_identifiers(self):
        for org, kind, handle, workspace in (
            ("../other", "metric", "metric_example", "workspace_example"),
            ("example-org", "output", "metric_example", "workspace_example"),
            ("example-org", "metric", "sql_query_example", "workspace_example"),
            ("example-org", "metric", "metric_example?x=1", "workspace_example"),
            ("example-org", "metric", "metric_example", "workspace_example&ws=other"),
        ):
            with self.subTest(kind=kind, handle=handle), self.assertRaises(ValueError):
                module.definition_link("https://app.atlan.com", org, kind, handle, workspace)


class AutomaticOrganizationTest(unittest.TestCase):
    def setUp(self):
        self.status = {"state": "authenticated", "gateway": "https://api.atlan.com",
                       "orgID": "org_active"}
        self.identity = {"org_id": "org_active", "orgs": [
            {"id": "org_other", "slug": "wrong-org"},
            {"id": "org_active", "slug": "example-org"},
        ]}
        self.artifact = {"id": "sql_query_example", "workspace_id": "workspace_example"}

    def resolve(self, responses):
        from unittest.mock import patch
        with patch.object(module, "_cli_json", side_effect=responses) as read:
            link = module.resolve_link("https://api.atlan.com", "https://app.atlan.com",
                                       "sql_query", "sql_query_example", "workspace_example")
            return link, read.call_args_list

    def test_uses_active_membership_not_first_row(self):
        link, calls = self.resolve([self.status, self.identity, self.artifact, self.status])
        self.assertIn("/example-org/apps/metric?", link)
        self.assertNotIn("wrong-org", link)
        self.assertEqual(calls[1].args[0][2], "registry:/users/me")
        self.assertEqual(calls[2].args[0][2], "registry:/artifacts/sql_query/sql_query_example")
        for call in calls:
            self.assertNotIn("token", call.args[0])

    def test_rejects_different_gateway(self):
        with self.assertRaisesRegex(ValueError, "MCP gateway"):
            self.resolve([{**self.status, "gateway": "https://api.example.com"}])

    def test_a_sign_in_without_an_organization_names_the_fix(self):
        with self.assertRaisesRegex(ValueError, "atlanai auth login"):
            self.resolve([{**self.status, "orgID": ""}])

    def test_an_expired_cli_sign_in_names_the_fix(self):
        from unittest.mock import patch
        import subprocess
        result = subprocess.CompletedProcess([], 4, stdout="", stderr="private error")
        with patch.object(module.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "atlanai auth login") as caught:
                module._cli_json(["api", "get", "registry:/users/me"])
            self.assertNotIn("private", str(caught.exception))

    def test_rejects_missing_or_ambiguous_active_membership(self):
        for orgs in ([], [{"id": "org_active"}],
                     [{"id": "org_active", "slug": "one"}, {"id": "org_active", "slug": "two"}]):
            with self.subTest(orgs=orgs), self.assertRaises(ValueError):
                self.resolve([self.status, {**self.identity, "orgs": orgs}])

    def test_rejects_profile_account_mismatch(self):
        with self.assertRaises(ValueError):
            self.resolve([self.status, {**self.identity, "org_id": "org_other"}])

    def test_rejects_artifact_from_another_workspace(self):
        with self.assertRaisesRegex(ValueError, "MCP workspace"):
            self.resolve([self.status, self.identity,
                          {**self.artifact, "workspace_id": "workspace_other"}])

    def test_rejects_concurrent_account_switch(self):
        with self.assertRaisesRegex(ValueError, "connection changed"):
            self.resolve([self.status, self.identity, self.artifact,
                          {**self.status, "orgID": "org_other"}])

    def test_cli_failures_do_not_echo_private_transport_details(self):
        from unittest.mock import patch
        import subprocess
        result = subprocess.CompletedProcess([], 1, stdout="private response", stderr="private error")
        with patch.object(module.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "metadata read failed") as caught:
                module._cli_json(["api", "get", "registry:/users/me"])
            self.assertNotIn("private", str(caught.exception))
