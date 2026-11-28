import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from workspace_sdk.deploy import deploy, load_deploy_settings
from workspace_sdk.ui_settings import load_ui_settings


class DeploySettingsTests(unittest.TestCase):
    def test_project_settings_and_cli_overrides_keep_the_ui_port_separate(self):
        with TemporaryDirectory() as temporary:
            project = Path(temporary)
            defaults = load_deploy_settings(project)
            self.assertEqual((defaults.ipv4, defaults.port, defaults.container_port, defaults.context),
                             ("127.0.0.1", 8080, 8000, None))
            (project / "setting.json").write_text(json.dumps({
                "port": 18081,
                "deploy": {"ipv4": "192.0.2.10", "port": 9090, "container_port": 8010, "context": "colima"},
            }))
            configured = load_deploy_settings(project)
            self.assertEqual((configured.ipv4, configured.port, configured.container_port, configured.context),
                             ("192.0.2.10", 9090, 8010, "colima"))
            overridden = load_deploy_settings(
                project, ipv4="0.0.0.0", port=0, container_port=8020, context="default",
            )
            self.assertEqual((overridden.ipv4, overridden.port, overridden.container_port, overridden.context),
                             ("0.0.0.0", 0, 8020, "default"))
            self.assertEqual(load_ui_settings(project, None).port, 18081)

    def test_invalid_settings_fail_before_docker_is_called(self):
        with TemporaryDirectory() as temporary:
            project = Path(temporary)
            path = project / "setting.json"
            for values, error in (
                ([], "deploy must be a JSON object"),
                ({"ipv4": "::1"}, "deploy.ipv4"),
                ({"port": True}, "deploy.port"),
                ({"container_port": 0}, "deploy.container_port"),
                ({"context": ""}, "deploy.context"),
                ({"host": "127.0.0.1"}, "Unknown deploy settings"),
            ):
                with self.subTest(values=values):
                    path.write_text(json.dumps({"deploy": values}))
                    with self.assertRaisesRegex(ValueError, error):
                        load_deploy_settings(project)
            with patch("workspace_sdk.deploy.subprocess.run") as docker:
                self.assertEqual(deploy(project, project), 1)
                docker.assert_not_called()
