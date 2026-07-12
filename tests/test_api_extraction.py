import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from proofloom.assertions import ExtractionError, OpenAICompatibleExtractor
from proofloom.entities import write_dictionary

from test_fixture_extraction import NOW, dictionary, fragments
from test_project_ui import RunningApplication, hidden_field, open_page, submit_form


def model_candidate(subject_id="entity_111111111111111111111111"):
    return {
        "id": "ast_api_synthetic",
        "subject_id": subject_id,
        "predicate": "VERIFIES",
        "object_id": "entity_222222222222222222222222",
        "primary_evidence_id": "src_signal",
        "supporting_evidence_ids": [],
    }


def response(candidate=None):
    content = json.dumps({"candidates": [candidate or model_candidate()]})
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


class ApiExtractionTests(unittest.TestCase):
    def test_adapter_uses_openai_chat_completions_contract_and_adds_api_provenance(self):
        captured = {}
        def transport(request, timeout):
            captured.update(url=request.full_url, headers=dict(request.header_items()), body=json.loads(request.data), timeout=timeout)
            return response()
        extractor = OpenAICompatibleExtractor(
            base_url="https://compatible.invalid/v1/",
            model="synthetic-model",
            api_key="super-secret",
            provider="synthetic-provider",
            transport=transport,
            clock=lambda: NOW,
        )
        candidates = extractor.extract(dictionary(), fragments())
        self.assertEqual("https://compatible.invalid/v1/chat/completions", captured["url"])
        self.assertEqual("Bearer super-secret", captured["headers"]["Authorization"])
        self.assertEqual("synthetic-model", captured["body"]["model"])
        self.assertEqual(30.0, captured["timeout"])
        self.assertNotIn("super-secret", json.dumps(captured["body"]))
        self.assertEqual("candidate", candidates[0]["status"])
        self.assertEqual({
            "provider": "synthetic-provider", "model": "synthetic-model",
            "prompt_version": "1", "schema_version": "1",
            "generated_at": "2026-01-02T03:04:05Z", "mode": "api",
        }, candidates[0]["extraction"])

    def test_environment_configuration_defaults_to_openai_and_requires_model_and_key(self):
        env = {"PROOFLOOM_OPENAI_MODEL": "gpt-synthetic", "PROOFLOOM_OPENAI_API_KEY": "secret"}
        with mock.patch.dict("os.environ", env, clear=True):
            extractor = OpenAICompatibleExtractor.from_environment(transport=lambda *_: response(), clock=lambda: NOW)
        self.assertEqual("openai", extractor.provider)
        self.assertEqual("https://api.openai.com/v1", extractor.base_url)
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ExtractionError, "PROOFLOOM_OPENAI_MODEL"):
                OpenAICompatibleExtractor.from_environment()

    def test_adapter_reports_http_json_and_response_shape_errors_without_leaking_key(self):
        cases = [
            (lambda *_: (_ for _ in ()).throw(urllib.error.HTTPError("https://x", 429, "rate", {}, None)), "HTTP status 429"),
            (lambda *_: b"not-json", "response JSON"),
            (lambda *_: b'{"choices":[]}', "choices.0.message.content"),
        ]
        for transport, message in cases:
            with self.subTest(message=message):
                extractor = OpenAICompatibleExtractor("https://x/v1", "m", "never-print-this", transport=transport)
                with self.assertRaisesRegex(ExtractionError, message) as caught:
                    extractor.extract(dictionary(), fragments())
                self.assertNotIn("never-print-this", str(caught.exception))

    def test_owner_runs_api_extraction_through_same_validation_and_valid_only_persistence(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "project"; project.mkdir()
            running = RunningApplication(Path(root))
            running.server.api_extractor = OpenAICompatibleExtractor("https://x/v1", "m", "secret", transport=lambda *_: response(model_candidate("missing")), clock=lambda: NOW)
            with running as app:
                home = open_page(app, "/")
                submit_form(app, "/projects/create", csrf_token=hidden_field(home, "csrf_token"), directory=str(project), name="API Project").read()
                write_dictionary(project / ".proofloom" / "entity-dictionary.json", dictionary())
                (project / ".proofloom" / "source-fragments.json").write_text(json.dumps(fragments()), encoding="utf-8")
                page = open_page(app, f"/assertions?project={project}")
                result = submit_form(app, "/assertions/extract-api", csrf_token=hidden_field(page, "csrf_token"), project=str(project)).read().decode()
                self.assertIn("subject_id", result)
            self.assertEqual([], json.loads((project / ".proofloom" / "candidate-assertions.json").read_text()))
            validation = json.loads((project / ".proofloom" / "assertion-validation.json").read_text())
            self.assertFalse(validation[0]["valid"])
            self.assertEqual("api", validation[0]["candidate"]["extraction"]["mode"])
            self.assertFalse((project / ".proofloom" / "query-graph.json").exists())
            self.assertNotIn("secret", "".join(path.read_text(errors="ignore") for path in project.rglob("*") if path.is_file()))


if __name__ == "__main__":
    unittest.main()
