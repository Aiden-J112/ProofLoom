import json
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from proofloom.assertions import FixtureExtractor, validate_candidates, write_extraction_results
from proofloom.entities import write_dictionary

from test_project_ui import RunningApplication, hidden_field, link_from, open_page, submit_form


NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def dictionary():
    return {
        "schema_version": "1",
        "entities": [
            {"id": "entity_111111111111111111111111", "canonical_name": "Inspector", "type": "Component", "aliases": ["Output Inspector"], "status": "accepted", "schema_version": "1"},
            {"id": "entity_222222222222222222222222", "canonical_name": "Report", "type": "Artifact", "aliases": ["Inspection Report"], "status": "accepted", "schema_version": "1"},
        ],
        "candidates": [],
    }


def fragments():
    return [{"id": "src_signal", "source_file": "synthetic-signal.md", "heading_path": ["Signal lesson"], "ordinal": 1, "kind": "paragraph", "content": "The Inspector verifies the generated Report.", "content_hash": "sha256:synthetic", "schema_version": "1"}]


class FixtureExtractionTests(unittest.TestCase):
    def test_fixture_is_deterministic_offline_and_records_provenance(self):
        extractor = FixtureExtractor(clock=lambda: NOW)
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network used")):
            first = extractor.extract(dictionary(), fragments())
            second = extractor.extract(dictionary(), fragments())
        self.assertEqual(first, second)
        self.assertEqual("fixture", first[0]["extraction"]["mode"])
        self.assertEqual("proofloom", first[0]["extraction"]["provider"])
        self.assertEqual("synthetic-signal-v1", first[0]["extraction"]["model"])
        self.assertEqual("2026-01-02T03:04:05Z", first[0]["extraction"]["generated_at"])

    def test_fixture_resolves_declared_locators_not_first_items(self):
        data = dictionary()
        data["entities"] = [
            {"id": "entity_333333333333333333333333", "canonical_name": "Unrelated", "type": "Component", "aliases": [], "status": "accepted", "schema_version": "1"},
            *reversed(data["entities"]),
        ]
        source_fragments = [
            {"id": "src_unrelated", "source_file": "other.md", "heading_path": ["Other"], "ordinal": 1, "kind": "paragraph", "content": "Unrelated.", "content_hash": "sha256:other", "schema_version": "1"},
            *fragments(),
        ]
        candidate = FixtureExtractor(clock=lambda: NOW).extract(data, source_fragments)[0]
        self.assertEqual("entity_111111111111111111111111", candidate["subject_id"])
        self.assertEqual("entity_222222222222222222222222", candidate["object_id"])
        self.assertEqual("src_signal", candidate["primary_evidence_id"])

    def test_each_predicate_enforces_its_exact_type_contract(self):
        contracts = {
            "COMPOSED_OF": ("Concept", "Component"),
            "PROMPTS": ("Artifact", "Component"),
            "CALLS_TOOL": ("Component", "Component"),
            "PRODUCES": ("Component", "Artifact"),
            "VERIFIES": ("Component", "Artifact"),
            "BLOCKS": ("Component", "Artifact"),
        }
        for predicate, (subject_type, object_type) in contracts.items():
            with self.subTest(predicate=predicate):
                data = dictionary()
                data["entities"][0]["type"] = subject_type
                data["entities"][1]["type"] = object_type
                base = FixtureExtractor(clock=lambda: NOW).extract(data, fragments())[0]
                base["predicate"] = predicate
                self.assertTrue(validate_candidates([base], data, fragments())[0]["valid"])
                data["entities"][1]["type"] = "Concept" if object_type != "Concept" else "Artifact"
                result = validate_candidates([base], data, fragments())[0]
                self.assertFalse(result["valid"])
                self.assertTrue(any(r["field"] == "predicate" for r in result["reasons"]))

    def test_validation_reports_field_paths_for_domain_failures(self):
        candidate = FixtureExtractor(clock=lambda: NOW).extract(dictionary(), fragments())[0]
        invalid = dict(candidate, subject_id="missing", primary_evidence_id="missing-fragment")
        wrong_contract = dict(candidate, object_id="entity_111111111111111111111111", predicate="BLOCKS")
        results = validate_candidates([invalid, wrong_contract], dictionary(), fragments())
        self.assertFalse(results[0]["valid"])
        paths = {reason["field"] for result in results for reason in result["reasons"]}
        self.assertIn("subject_id", paths)
        self.assertIn("primary_evidence_id", paths)
        self.assertIn("predicate", paths)

    def test_executable_schema_rejects_malformed_evidence_and_provenance(self):
        candidate = FixtureExtractor(clock=lambda: NOW).extract(dictionary(), fragments())[0]
        malformed = dict(candidate, supporting_evidence_ids=["src_signal", "src_signal"])
        malformed["extraction"] = dict(candidate["extraction"], generated_at="not-a-time")
        result = validate_candidates([malformed], dictionary(), fragments())[0]
        self.assertFalse(result["valid"])
        schema_fields = {reason["field"] for reason in result["reasons"] if reason["rule"] == "schema"}
        self.assertEqual({"extraction.generated_at", "supporting_evidence_ids"}, schema_fields)

    def test_executable_schema_accepts_api_mode_and_rejects_unknown_mode(self):
        candidate = FixtureExtractor(clock=lambda: NOW).extract(dictionary(), fragments())[0]
        candidate["extraction"] = dict(candidate["extraction"], mode="api")
        self.assertTrue(validate_candidates([candidate], dictionary(), fragments())[0]["valid"])
        candidate["extraction"]["mode"] = "mystery"
        reasons = validate_candidates([candidate], dictionary(), fragments())[0]["reasons"]
        self.assertTrue(any(r["field"] == "extraction.mode" and r["rule"] == "schema" for r in reasons))

    def test_malformed_contexts_return_reasons_instead_of_raising(self):
        candidate = FixtureExtractor(clock=lambda: NOW).extract(dictionary(), fragments())[0]
        cases = [
            ([candidate], [], fragments()),
            ([candidate], {"entities": [None, {"status": "accepted"}]}, [None, {}]),
            ([candidate], {"entities": None}, None),
            ([candidate], {"entities": [dict(dictionary()["entities"][0], type=[]), dictionary()["entities"][1]]}, fragments()),
            ([None, {"supporting_evidence_ids": 7}], dictionary(), fragments()),
        ]
        for candidates, entities, source_fragments in cases:
            with self.subTest(candidates=candidates, entities=entities):
                results = validate_candidates(candidates, entities, source_fragments)
                self.assertTrue(results)
                self.assertTrue(any(result["reasons"] for result in results))

    def test_duplicate_candidate_ids_invalidate_every_conflicting_item(self):
        valid = FixtureExtractor(clock=lambda: NOW).extract(dictionary(), fragments())[0]
        invalid = dict(valid, primary_evidence_id="missing")
        results = validate_candidates([valid, invalid], dictionary(), fragments())
        self.assertEqual([False, False], [result["valid"] for result in results])
        for result in results:
            duplicate_reasons = [
                reason for reason in result["reasons"]
                if reason["field"] == "id" and reason["rule"] == "duplicate"
            ]
            self.assertEqual(1, len(duplicate_reasons))
            self.assertIn(valid["id"], duplicate_reasons[0]["reason"])
            self.assertEqual(valid["id"], result["candidate"]["id"])

    def test_unresolved_evidence_placeholder_is_derived_from_fixture_locator(self):
        from proofloom import assertions
        locator = {"source_file": "custom.md", "heading_path": ["One", "Two"], "ordinal": 7}
        with mock.patch.dict(assertions._FIXTURE["evidence"], locator, clear=True):
            candidate = FixtureExtractor(clock=lambda: NOW).extract(dictionary(), [fragments()[0]])[0]
        self.assertEqual(
            "fixture.unresolved.evidence:custom.md#One/Two:p7",
            candidate["primary_evidence_id"],
        )

    def test_second_result_write_failure_restores_consistent_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            validation_path = Path(root) / "validation.json"
            candidates_path = Path(root) / "candidates.json"
            validation_path.write_text('[{"old":"validation"}]', encoding="utf-8")
            candidates_path.write_text('[{"old":"candidate"}]', encoding="utf-8")
            from proofloom import assertions
            original = assertions.write_json_atomic
            calls = 0
            def fail_second(path, data):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("disk full")
                return original(path, data)
            with mock.patch("proofloom.assertions.write_json_atomic", side_effect=fail_second):
                with self.assertRaises(OSError):
                    write_extraction_results(validation_path, candidates_path, [{"new": "validation"}], [{"new": "candidate"}])
            self.assertEqual([{"old": "validation"}], json.loads(validation_path.read_text()))
            self.assertEqual([{"old": "candidate"}], json.loads(candidates_path.read_text()))

    def test_owner_runs_fixture_in_project_ui_and_sees_persisted_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "project"
            project.mkdir()
            with RunningApplication(Path(root)) as app:
                home = open_page(app, "/")
                page = submit_form(app, "/projects/create", csrf_token=hidden_field(home, "csrf_token"), directory=str(project), name="Fixture Project").read().decode()
                write_dictionary(project / ".proofloom" / "entity-dictionary.json", dictionary())
                (project / ".proofloom" / "source-fragments.json").write_text(json.dumps(fragments()), encoding="utf-8")
                extraction = open_page(app, link_from(page, "Extract Candidate Assertions"))
                result = submit_form(app, "/assertions/extract-fixture", csrf_token=hidden_field(extraction, "csrf_token"), project=str(project)).read().decode()
                self.assertIn("Candidate Assertions", result)
                self.assertIn("entity_111111111111111111111111", result)
                self.assertIn("src_signal", result)
                self.assertIn("synthetic-signal.md", result)
                self.assertIn("fixture", result)

            persisted = json.loads((project / ".proofloom" / "candidate-assertions.json").read_text())
            validation = json.loads((project / ".proofloom" / "assertion-validation.json").read_text())
            self.assertEqual(1, len(persisted))
            self.assertTrue(validation[0]["valid"])
            self.assertFalse((project / ".proofloom" / "query-graph.json").exists())

    def test_invalid_fixture_result_is_only_persisted_in_validation_output(self):
        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "project"
            project.mkdir()
            with RunningApplication(Path(root)) as app:
                home = open_page(app, "/")
                page = submit_form(app, "/projects/create", csrf_token=hidden_field(home, "csrf_token"), directory=str(project), name="Invalid Fixture Project").read().decode()
                (project / ".proofloom" / "source-fragments.json").write_text(json.dumps(fragments()), encoding="utf-8")
                extraction = open_page(app, link_from(page, "Extract Candidate Assertions"))
                result = submit_form(app, "/assertions/extract-fixture", csrf_token=hidden_field(extraction, "csrf_token"), project=str(project)).read().decode()
                self.assertIn("subject_id: &#x27;fixture.unresolved.subject:Output Inspector&#x27; must reference an accepted Entity Dictionary entry", result)
                self.assertIn("object_id: &#x27;fixture.unresolved.object:Inspection Report&#x27; must reference an accepted Entity Dictionary entry", result)

            self.assertEqual([], json.loads((project / ".proofloom" / "candidate-assertions.json").read_text()))
            validation = json.loads((project / ".proofloom" / "assertion-validation.json").read_text())
            self.assertFalse(validation[0]["valid"])
            self.assertEqual("fixture.unresolved.subject:Output Inspector", validation[0]["candidate"]["subject_id"])
            self.assertFalse((project / ".proofloom" / "query-graph.json").exists())

    def test_duplicate_fixture_ids_are_all_validation_only_in_ui(self):
        class DuplicateExtractor:
            def extract(self, entity_dictionary, source_fragments):
                valid = FixtureExtractor(clock=lambda: NOW).extract(entity_dictionary, source_fragments)[0]
                return [valid, dict(valid, primary_evidence_id="missing")]

        with tempfile.TemporaryDirectory() as root:
            project = Path(root) / "project"
            project.mkdir()
            runner = RunningApplication(Path(root))
            runner.server.fixture_extractor = DuplicateExtractor()
            with runner as app:
                home = open_page(app, "/")
                page = submit_form(app, "/projects/create", csrf_token=hidden_field(home, "csrf_token"), directory=str(project), name="Duplicate Fixture Project").read().decode()
                write_dictionary(project / ".proofloom" / "entity-dictionary.json", dictionary())
                (project / ".proofloom" / "source-fragments.json").write_text(json.dumps(fragments()), encoding="utf-8")
                extraction = open_page(app, link_from(page, "Extract Candidate Assertions"))
                submit_form(app, "/assertions/extract-fixture", csrf_token=hidden_field(extraction, "csrf_token"), project=str(project)).read()

            self.assertEqual([], json.loads((project / ".proofloom" / "candidate-assertions.json").read_text()))
            validation = json.loads((project / ".proofloom" / "assertion-validation.json").read_text())
            self.assertEqual(2, len(validation))
            self.assertTrue(all(not item["valid"] for item in validation))
            self.assertTrue(all(any(r["field"] == "id" and r["rule"] == "duplicate" for r in item["reasons"]) for item in validation))
            self.assertFalse((project / ".proofloom" / "query-graph.json").exists())


if __name__ == "__main__":
    unittest.main()
