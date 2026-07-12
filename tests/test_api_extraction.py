import gc
import json
import tempfile
import unittest
import urllib.error
import warnings
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
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


def response(candidate=None, candidates=None):
    content = json.dumps({"candidates": candidates if candidates is not None else [candidate or model_candidate()]})
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


class ApiExtractionTests(unittest.TestCase):
    def test_default_transport_never_forwards_authorization_across_redirects(self):
        destination_requests = []
        source_authorization = []

        class DestinationHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                destination_requests.append(dict(self.headers))
                self.send_response(200); self.end_headers()
            do_POST = do_GET
            def log_message(self, *_): pass

        with warnings.catch_warnings():
            warnings.simplefilter("error", ResourceWarning)
            destination = ThreadingHTTPServer(("127.0.0.1", 0), DestinationHandler)
            destination_thread = Thread(target=destination.serve_forever); destination_thread.start()
            try:
                for status in (301, 302, 303, 307, 308):
                    class RedirectHandler(BaseHTTPRequestHandler):
                        def do_POST(self):
                            self.rfile.read(int(self.headers.get("Content-Length", "0")))
                            source_authorization.append(self.headers.get("Authorization"))
                            self.send_response(status)
                            self.send_header("Location", f"http://127.0.0.1:{destination.server_port}/capture")
                            self.send_header("Content-Length", "0")
                            self.send_header("Connection", "close")
                            self.end_headers()
                        def log_message(self, *_): pass

                    source = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
                    source_thread = Thread(target=source.serve_forever); source_thread.start()
                    try:
                        extractor = OpenAICompatibleExtractor(f"http://127.0.0.1:{source.server_port}/chat/completions", "m", "redirect-secret")
                        with self.assertRaisesRegex(ExtractionError, f"HTTP status {status}") as caught:
                            extractor.extract(dictionary(), fragments())
                        self.assertNotIn("redirect-secret", str(caught.exception))
                        self.assertNotIn("capture", str(caught.exception))
                    finally:
                        source.shutdown(); source_thread.join(); source.server_close()
                self.assertEqual(["Bearer redirect-secret"] * 5, source_authorization)
                self.assertEqual([], destination_requests)
            finally:
                destination.shutdown(); destination_thread.join(); destination.server_close()
            gc.collect()

    def test_default_transport_still_accepts_normal_chat_completion_response(self):
        class SuccessHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(200)
                body = response()
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers(); self.wfile.write(body)
            def log_message(self, *_): pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), SuccessHandler)
        thread = Thread(target=server.serve_forever); thread.start()
        try:
            extractor = OpenAICompatibleExtractor(f"http://127.0.0.1:{server.server_port}/chat/completions", "m", "secret", clock=lambda: NOW)
            self.assertEqual("ast_api_synthetic", extractor.extract(dictionary(), fragments())[0]["id"])
        finally:
            server.shutdown(); thread.join(); server.server_close()

    def test_adapter_uses_openai_chat_completions_contract_and_adds_api_provenance(self):
        captured = {}
        def transport(request, timeout):
            captured.update(url=request.full_url, headers=dict(request.header_items()), body=json.loads(request.data), timeout=timeout)
            return response()
        extractor = OpenAICompatibleExtractor(
            endpoint="https://compatible.invalid/custom/chat/completions",
            model="synthetic-model",
            api_key="super-secret",
            provider="synthetic-provider",
            transport=transport,
            clock=lambda: NOW,
        )
        candidates = extractor.extract(dictionary(), fragments())
        self.assertEqual("https://compatible.invalid/custom/chat/completions", captured["url"])
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
        self.assertEqual("https://api.openai.com/v1/chat/completions", extractor.endpoint)
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
                extractor = OpenAICompatibleExtractor("https://x.invalid/chat/completions", "m", "never-print-this", transport=transport)
                with self.assertRaisesRegex(ExtractionError, message) as caught:
                    extractor.extract(dictionary(), fragments())
                self.assertNotIn("never-print-this", str(caught.exception))

    def test_owner_runs_api_extraction_through_same_validation_and_valid_only_persistence(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "project"; project.mkdir()
            running = RunningApplication(Path(root))
            valid = model_candidate()
            valid.update(status="accepted", extraction={"provider": "forged"})
            missing = model_candidate(); missing["id"] = "ast_missing"; missing.pop("predicate")
            extra = dict(model_candidate(), id="ast_extra", injected=True)
            replacement = dict(model_candidate(), id="ast_replace", replaces_assertion_id="ast_existing")
            proposals = [valid, missing, extra, 7, replacement]
            running.server.api_extractor = OpenAICompatibleExtractor("https://x.invalid/chat/completions", "m", "secret", transport=lambda *_: response(candidates=proposals), clock=lambda: NOW)
            with running as app:
                home = open_page(app, "/")
                submit_form(app, "/projects/create", csrf_token=hidden_field(home, "csrf_token"), directory=str(project), name="API Project").read()
                write_dictionary(project / ".proofloom" / "entity-dictionary.json", dictionary())
                (project / ".proofloom" / "source-fragments.json").write_text(json.dumps(fragments()), encoding="utf-8")
                page = open_page(app, f"/assertions?project={project}")
                result = submit_form(app, "/assertions/extract-api", csrf_token=hidden_field(page, "csrf_token"), project=str(project)).read().decode()
                self.assertIn("predicate", result)
            persisted = json.loads((project / ".proofloom" / "candidate-assertions.json").read_text())
            self.assertEqual(["ast_api_synthetic"], [item["id"] for item in persisted])
            self.assertEqual("candidate", persisted[0]["status"])
            self.assertEqual("api", persisted[0]["extraction"]["mode"])
            validation = json.loads((project / ".proofloom" / "assertion-validation.json").read_text())
            self.assertEqual([True, False, False, False, False], [item["valid"] for item in validation])
            self.assertEqual(7, validation[3]["candidate"])
            fields = [{reason["field"] for reason in item["reasons"]} for item in validation]
            self.assertIn("predicate", fields[1])
            self.assertIn("$", fields[2])
            self.assertIn("$", fields[3])
            self.assertIn("replaces_assertion_id", fields[4])
            self.assertFalse((project / ".proofloom" / "query-graph.json").exists())
            self.assertNotIn("secret", "".join(path.read_text(errors="ignore") for path in project.rglob("*") if path.is_file()))

    def test_custom_endpoint_environment_and_endpoint_safety_rules(self):
        env = {
            "PROOFLOOM_OPENAI_ENDPOINT": "https://provider.invalid/api/chat/completions",
            "PROOFLOOM_OPENAI_MODEL": "provider-model",
            "PROOFLOOM_OPENAI_API_KEY": "secret",
            "PROOFLOOM_OPENAI_PROVIDER": "compatible",
        }
        with mock.patch.dict("os.environ", env, clear=True):
            extractor = OpenAICompatibleExtractor.from_environment()
        self.assertEqual("https://provider.invalid/api/chat/completions", extractor.endpoint)
        self.assertEqual("compatible", extractor.provider)

        invalid = [
            ("ftp://example.com/chat/completions", 30),
            ("https://user:pass@example.com/chat/completions", 30),
            ("http://example.com/chat/completions", 30),
            ("https:///chat/completions", 30),
            ("https://example.com/chat/completions", 0),
            ("https://example.com/chat/completions", float("inf")),
        ]
        for endpoint, timeout in invalid:
            with self.subTest(endpoint=endpoint, timeout=timeout):
                with self.assertRaises(ExtractionError):
                    OpenAICompatibleExtractor(endpoint, "m", "secret", timeout=timeout)
        OpenAICompatibleExtractor("http://localhost:8001/v1/chat/completions", "m", "secret")
        OpenAICompatibleExtractor("http://127.0.0.1:8001/v1/chat/completions", "m", "secret")


if __name__ == "__main__":
    unittest.main()
