import json
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from proofloom.app import main
from proofloom.assertions import CodexCliExtractor, OpenAICompatibleExtractor
from proofloom.entities import write_dictionary

from test_api_extraction import model_candidate
from test_fixture_extraction import NOW, dictionary, fragments
from test_project_ui import RunningApplication, hidden_field, open_page, submit_form


class _OneShotServer:
    server_port = 4321

    def serve_forever(self):
        return None

    def server_close(self):
        return None


class RuntimeConfigCliTests(unittest.TestCase):
    def test_cli_config_resolves_server_and_codex_extractor(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            config_path = root / "launch.json"
            config_path.write_text(json.dumps({
                "server": {"host": "LOCALHOST", "port": 4321, "browse_root": "."},
                "llm": {"backend": "codex-cli", "model": "gpt-5.6-luna", "reasoning": "medium"},
            }), encoding="utf-8")
            captured = {}

            def server_factory(host, port, browse_root, configured_extractor=None):
                captured.update(host=host, port=port, browse_root=browse_root, extractor=configured_extractor)
                return _OneShotServer()

            with mock.patch("proofloom.app.create_server", side_effect=server_factory):
                with mock.patch("sys.argv", ["proofloom", "--config", str(config_path)]):
                    main()

            self.assertEqual("localhost", captured["host"])
            self.assertEqual(4321, captured["port"])
            self.assertEqual(root.resolve(), captured["browse_root"])
            self.assertIsInstance(captured["extractor"], CodexCliExtractor)
            self.assertEqual("gpt-5.6-luna", captured["extractor"].model)
            self.assertEqual("medium", captured["extractor"].reasoning)

    def test_cli_config_selects_openai_compatible_backend_and_cli_overrides_server(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            config_path = root / "launch.json"
            config_path.write_text(json.dumps({
                "server": {"host": "localhost", "port": 4321, "browse_root": "."},
                "llm": {
                    "backend": "openai-compatible",
                    "api_key": "top-secret-value",
                    "model": "configured-model",
                    "base_url": "https://provider.invalid/v1",
                    "provider": "configured-provider",
                    "timeout": 12,
                },
            }), encoding="utf-8")
            captured = {}

            def server_factory(host, port, browse_root, configured_extractor=None):
                captured.update(host=host, port=port, browse_root=browse_root, extractor=configured_extractor)
                return _OneShotServer()

            with mock.patch("proofloom.app.create_server", side_effect=server_factory):
                with mock.patch("sys.argv", ["proofloom", "--config", str(config_path), "--host", "127.0.0.1", "--port", "8765"]):
                    main()

            self.assertEqual("127.0.0.1", captured["host"])
            self.assertEqual(8765, captured["port"])
            extractor = captured["extractor"]
            self.assertIsInstance(extractor, OpenAICompatibleExtractor)
            self.assertEqual("https://provider.invalid/v1/chat/completions", extractor.endpoint)
            self.assertEqual("configured-model", extractor.model)
            self.assertEqual("configured-provider", extractor.provider)
            self.assertNotIn("top-secret-value", repr(extractor))

    def test_cli_rejects_unknown_and_unsafe_config_without_leaking_values(self):
        invalid_documents = [
            {"server": {"host": "0.0.0.0", "port": 8000, "browse_root": "."}},
            {"server": {"browse_root": ".", "unexpected": "top-secret-value"}},
            {"server": {"browse_root": "missing-directory"}},
            {"llm": {"backend": "openai-compatible", "api_key": "top-secret-value", "model": "m", "endpoint": "https://top-secret-value@example.invalid/v1"}},
        ]
        for document in invalid_documents:
            with self.subTest(document=list(document)):
                with tempfile.TemporaryDirectory() as root_name:
                    path = Path(root_name) / "launch.json"
                    path.write_text(json.dumps(document), encoding="utf-8")
                    stderr = io.StringIO()
                    with mock.patch("sys.argv", ["proofloom", "--config", str(path)]):
                        with mock.patch("proofloom.app.create_server", side_effect=AssertionError("invalid config reached server")):
                            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
                                main()
                    self.assertEqual(2, caught.exception.code)
                    self.assertNotIn("top-secret-value", stderr.getvalue())


class CodexCliExtractionTests(unittest.TestCase):
    def test_default_resolver_prefers_a_direct_platform_executable(self):
        captured = {}

        def runner(command, **kwargs):
            captured["command"] = command
            Path(command[command.index("-o") + 1]).write_text(
                '{"candidates": []}', encoding="utf-8"
            )
            return subprocess.CompletedProcess(command, 0)

        with mock.patch("proofloom.assertions.shutil.which", return_value="resolved-codex-binary") as which:
            CodexCliExtractor("gpt-5.6-luna", "medium", runner=runner).extract(
                dictionary(), fragments()
            )

        which.assert_called_once_with("codex.exe" if os.name == "nt" else "codex")
        self.assertEqual("resolved-codex-binary", captured["command"][0])

    def test_configured_codex_run_is_isolated_and_persists_candidate_only(self):
        captured = {}

        def runner(command, **kwargs):
            captured.update(command=command, **kwargs)
            schema_path = Path(command[command.index("--output-schema") + 1])
            result_path = Path(command[command.index("-o") + 1])
            captured["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
            captured["cwd_exists"] = Path(kwargs["cwd"]).is_dir()
            result_path.write_text(json.dumps({"candidates": [model_candidate()]}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="ignored", stderr="ignored")

        extractor = CodexCliExtractor(
            "gpt-5.6-luna",
            "medium",
            runner=runner,
            executable_resolver=lambda: r"C:\WindowsApps\codex.exe",
            clock=lambda: NOW,
        )
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = root / "project"
            project.mkdir()
            running = RunningApplication(root)
            running.server.api_extractor = extractor
            with running as app:
                home = open_page(app, "/")
                submit_form(app, "/projects/create", csrf_token=hidden_field(home, "csrf_token"), directory=str(project), name="Codex Project").read()
                write_dictionary(project / ".proofloom" / "entity-dictionary.json", dictionary())
                (project / ".proofloom" / "source-fragments.json").write_text(json.dumps(fragments()), encoding="utf-8")
                page = open_page(app, f"/assertions?project={project}")
                result = submit_form(app, "/assertions/extract-api", csrf_token=hidden_field(page, "csrf_token"), project=str(project)).read().decode()
                self.assertIn("provider=codex-cli", result)

            command = captured["command"]
            self.assertEqual(r"C:\WindowsApps\codex.exe", command[0])
            self.assertEqual("exec", command[1])
            for flag in ("--ephemeral", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules", "--strict-config", "--output-schema", "-o"):
                self.assertIn(flag, command)
            self.assertNotIn("--sandbox", command)
            config_values = [
                command[index + 1]
                for index, value in enumerate(command)
                if value in {"-c", "--config"}
            ]
            self.assertIn('default_permissions="proofloom"', config_values)
            self.assertIn('permissions.proofloom.filesystem.":minimal"="read"', config_values)
            self.assertIn('permissions.proofloom.filesystem.":workspace_roots"="read"', config_values)
            self.assertIn("permissions.proofloom.network.enabled=false", config_values)
            self.assertIn('web_search="disabled"', config_values)
            self.assertIn('shell_environment_policy.inherit="none"', config_values)
            self.assertIn("allow_login_shell=false", config_values)
            self.assertEqual("gpt-5.6-luna", command[command.index("--model") + 1])
            self.assertIn('model_reasoning_effort="medium"', command)
            self.assertEqual("-", command[-1])
            self.assertTrue(captured["cwd_exists"])
            self.assertNotEqual(Path.cwd().resolve(), Path(captured["cwd"]).resolve())
            self.assertIn("source_fragments", captured["input"])
            self.assertNotIn("source_fragments", " ".join(command))
            self.assertFalse(captured.get("shell", False))
            self.assertEqual(120, captured["timeout"])
            self.assertEqual("object", captured["schema"]["type"])

            persisted = json.loads((project / ".proofloom" / "candidate-assertions.json").read_text(encoding="utf-8"))
            self.assertEqual("candidate", persisted[0]["status"])
            self.assertEqual({
                "provider": "codex-cli", "model": "gpt-5.6-luna",
                "prompt_version": "1", "schema_version": "1",
                "generated_at": "2026-01-02T03:04:05Z", "mode": "codex-cli",
            }, persisted[0]["extraction"])
            self.assertFalse((project / ".proofloom" / "query-graph.json").exists())

    def test_codex_failures_are_safe_and_never_expose_process_output(self):
        def no_output(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="top-secret-output", stderr="top-secret-error")

        def bad_json(command, **kwargs):
            Path(command[command.index("-o") + 1]).write_text("not-json", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="top-secret-output", stderr="top-secret-error")

        cases = [
            (lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()), "not found"),
            (lambda command, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired(command, 1, stderr="top-secret-error")), "timed out"),
            (lambda command, **_kwargs: subprocess.CompletedProcess(command, 17, stdout="top-secret-output", stderr="top-secret-error"), "exit status 17"),
            (no_output, "did not produce"),
            (bad_json, "invalid JSON"),
        ]
        for runner, expected in cases:
            with self.subTest(expected=expected):
                extractor = CodexCliExtractor(
                    "gpt-5.6-luna",
                    "medium",
                    runner=runner,
                    executable_resolver=lambda: "codex-test-binary",
                )
                with self.assertRaisesRegex(ValueError, expected) as caught:
                    extractor.extract(dictionary(), fragments())
                message = str(caught.exception)
                self.assertNotIn("top-secret-output", message)
                self.assertNotIn("top-secret-error", message)

    def test_missing_direct_executable_is_reported_without_starting_a_shell(self):
        runner = mock.Mock()
        extractor = CodexCliExtractor(
            "gpt-5.6-luna",
            "medium",
            runner=runner,
            executable_resolver=lambda: None,
        )
        with self.assertRaisesRegex(ValueError, "executable was not found"):
            extractor.extract(dictionary(), fragments())
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
