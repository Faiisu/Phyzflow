import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import tomllib
import unittest


FLOW = """\
from pathlib import Path
import sys


def reject_network(event, args):
    if event.startswith("socket."):
        raise RuntimeError("The model fixture must work offline.")


sys.addaudithook(reject_network)

from model import predict
from workspace_sdk import Flow, Input, Output, Step

Path("flow-imported").touch()
flow = Flow(
    inputs=("text",),
    steps=(Step("Predict", predict, {"text": Input("text")}),),
    output=Output("Predict"),
)
"""
MODEL = """\
import json
from pathlib import Path


def predict(text):
    Path("node-called").touch()
    weights = json.loads(Path("weights.json").read_text())
    return {"score": len(text) * weights["scale"]}
"""


class ValidationTests(unittest.TestCase):
    def invoke(self, root, command, *options):
        executable = Path(sys.executable).with_name("workspace")
        args = [str(executable), command, *options]
        if command == "run":
            args.append('{"text":"hello"}')
        return subprocess.run(args, cwd=root, capture_output=True, text=True)

    def test_workspace_format_is_checked_before_flow_import(self):
        for declaration, expected in (
            ("format_version = 1", None),
            ("format_version = 2", "found 2"),
            ('format_version = "1"', "found '1'"),
            ("format_version = true", "found True"),
            ("format_version = 1.0", "found 1.0"),
        ):
            combination = tomllib.loads(declaration)["format_version"]
            with self.subTest(combination=combination), TemporaryDirectory() as temp:
                root = Path(temp).resolve()
                config = root / "pyproject.toml"
                config.write_text(f"[tool.workspace]\n{declaration}\n")
                (root / "flow.py").write_text(FLOW)
                (root / "model.py").write_text(MODEL)

                result = self.invoke(root, "check")
                self.assertEqual(result.returncode, 0 if expected is None else 1, result.stderr)
                self.assertEqual((root / "flow-imported").exists(), expected is None)
                if expected is not None:
                    self.assertIn(str(config), result.stderr)
                    self.assertIn("format_version", result.stderr)
                    self.assertIn(expected, result.stderr)
                    self.assertIn("supported version is 1", result.stderr)

    def test_unspecified_format_keeps_existing_source_validation(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            config = root / "pyproject.toml"
            original = b'[tool.workspace]\nsources = ["nested.py"]\n'
            config.write_bytes(original)
            (root / "flow.py").write_text(FLOW)
            (root / "model.py").write_text(MODEL)
            nested = root / "nested.py"
            nested.write_text("def broken(:\n")

            invalid = self.invoke(root, "check")
            self.assertEqual(invalid.returncode, 1, invalid.stderr)
            self.assertIn("Workspace format is unspecified", invalid.stderr)
            self.assertIn("nested.py:1", invalid.stderr)
            self.assertFalse((root / "flow-imported").exists())
            nested.write_text("value = 1\n")

            valid = self.invoke(root, "check")
            self.assertEqual(valid.returncode, 0, valid.stderr)
            self.assertIn("Workspace format is unspecified", valid.stderr)
            self.assertEqual(config.read_bytes(), original)

    def test_child_sheet_uses_the_workspace_root_format(self):
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            sheet = root / "flows" / "Predict"
            sheet.mkdir(parents=True)
            config = root / "pyproject.toml"
            config.write_text("[tool.workspace]\nformat_version = 2\n")
            (sheet / "pyproject.toml").write_text("[tool.workspace]\nformat_version = 1\n")
            (sheet / "flow.py").write_text(FLOW)
            (sheet / "model.py").write_text(MODEL)

            for folder, options in ((root, ("--sheet", "Predict")), (sheet, ())):
                with self.subTest(folder=folder):
                    result = self.invoke(folder, "check", *options)
                    self.assertEqual(result.returncode, 1, result.stderr)
                    self.assertIn(str(config), result.stderr)
                    self.assertFalse((sheet / "flow-imported").exists())

            config.write_text("[tool.workspace]\nformat_version = 1\n")
            (sheet / "pyproject.toml").write_text("[tool.workspace]\nformat_version = 2\n")
            for folder, options in ((root, ("--sheet", "Predict")), (sheet, ())):
                with self.subTest(folder=folder):
                    result = self.invoke(folder, "check", *options)
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_project_does_not_call_nodes(self):
        cases = {
            "syntax": (FLOW, "Syntax:", True),
            "import": (FLOW.replace("from model import", "from absent import"), "Flow import failed.", False),
            "entrypoint": (FLOW.replace("flow =", "other ="), "Entrypoint:", False),
            "dependency": (FLOW.replace('Input("text")', 'Output("Missing")'), "unknown step 'Missing'", False),
            "cycle": (FLOW.replace('Input("text")', 'Output("Predict")'), "Dependency cycle:", False),
            "argument name": (FLOW.replace('{"text": Input', '{"wrong": Input'), "Invalid arguments for Predict", False),
            "missing argument": (FLOW.replace('{"text": Input("text")}', "{}"), "Invalid arguments for Predict", False),
        }
        for name, (flow_code, diagnostic, syntax_error) in cases.items():
            for command in ("check", "run"):
                with self.subTest(case=name, command=command), TemporaryDirectory() as temp:
                    root = Path(temp)
                    (root / "flow.py").write_text(flow_code)
                    (root / "model.py").write_text(MODEL)
                    (root / "nodes").mkdir()
                    (root / "pyproject.toml").write_text('[tool.workspace]\nsources = ["nodes"]\n')
                    (root / "nodes" / "nested.py").write_text("def broken(:\n" if syntax_error else "value = 1\n")

                    result = self.invoke(root, command)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(diagnostic, result.stderr)
                    self.assertFalse((root / "node-called").exists())
                    if syntax_error:
                        self.assertIn("nested.py:1", result.stderr)
                        self.assertFalse((root / "flow-imported").exists())
                    elif name.startswith("argument") or name == "missing argument":
                        self.assertIn("model.py:5", result.stderr)
                    else:
                        self.assertIn("flow.py", result.stderr)

    def test_check_passes_before_the_model_weights_are_available(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "flow.py").write_text(FLOW)
            (root / "model.py").write_text(MODEL)
            result = self.invoke(root, "check")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / "node-called").exists())

            (root / "weights.json").write_text('{"scale":2}')
            result = self.invoke(root, "run")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {"score": 10})
            self.assertTrue((root / "node-called").exists())


if __name__ == "__main__":
    unittest.main()
