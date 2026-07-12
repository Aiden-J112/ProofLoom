import json
import tempfile
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from pathlib import Path

from proofloom.assertions import write_json_atomic
from proofloom.entities import write_dictionary
from proofloom.reviews import ReviewError, append_event, fold_status, load_events

from test_fixture_extraction import NOW, dictionary, fragments
from test_project_ui import RunningApplication, hidden_field, open_page, submit_form


def candidate(assertion_id="ast_review_original"):
    return {
        "id": assertion_id,
        "subject_id": "entity_111111111111111111111111",
        "predicate": "VERIFIES",
        "object_id": "entity_222222222222222222222222",
        "primary_evidence_id": "src_signal",
        "supporting_evidence_ids": [],
        "status": "candidate",
        "extraction": {
            "provider": "proofloom",
            "model": "synthetic-review-v1",
            "prompt_version": "1",
            "schema_version": "1",
            "generated_at": "2026-01-02T03:04:05Z",
            "mode": "fixture",
        },
    }


def create_review_project(root: Path, assertion=None):
    project = root / "project"
    project.mkdir()
    metadata = project / ".proofloom"
    metadata.mkdir()
    (metadata / "project.json").write_text(
        '{"schema_version":"1","project":{"name":"Review Project"}}',
        encoding="utf-8",
    )
    write_dictionary(metadata / "entity-dictionary.json", dictionary())
    write_json_atomic(metadata / "source-fragments.json", fragments())
    write_json_atomic(metadata / "candidate-assertions.json", [assertion or candidate()])
    return project


class AssertionReviewUiTests(unittest.TestCase):
    def test_append_sequence_not_clock_orders_state_and_filenames(self):
        with tempfile.TemporaryDirectory() as root_name:
            directory = Path(root_name)
            common = {"assertion_id": "ast_review_original", "reviewer": "local-user", "replacement_assertion_id": None, "replacement_assertion": None, "note": None, "schema_version": "1"}
            first = dict(common, id="rev_11111111111111111111111111111111", action="accept", reviewed_at="2026-01-02T03:04:05Z")
            second = dict(common, id="rev_22222222222222222222222222222222", action="needs_domain_review", reviewed_at="2025-01-02T03:04:05Z")
            append_event(directory, first)
            append_event(directory, second)
            events = load_events(directory)
            self.assertEqual([1, 2], [item["sequence"] for item in events])
            self.assertEqual("needs_domain_review", fold_status("ast_review_original", events))
            self.assertEqual(["00000000000000000001.json", "00000000000000000002.json"], sorted(path.name for path in directory.glob("*.json")))

    def test_append_allocator_retries_competing_writers_without_duplicate_sequences(self):
        with tempfile.TemporaryDirectory() as root_name:
            directory = Path(root_name)
            def append(index):
                event = {"id": f"rev_{index:032x}", "assertion_id": "ast_review_original", "action": "accept", "reviewer": "local-user", "reviewed_at": "2026-01-02T03:04:05Z", "replacement_assertion_id": None, "replacement_assertion": None, "note": None, "schema_version": "1"}
                return append_event(directory, event)["sequence"]
            with ThreadPoolExecutor(max_workers=12) as pool:
                sequences = list(pool.map(append, range(1, 25)))
            self.assertEqual(list(range(1, 25)), sorted(sequences))
            self.assertEqual(list(range(1, 25)), [event["sequence"] for event in load_events(directory)])

    def test_review_event_schema_conditions_report_field_paths(self):
        with tempfile.TemporaryDirectory() as root_name:
            directory = Path(root_name)
            base = {"id": "rev_11111111111111111111111111111111", "assertion_id": "ast_review_original", "action": "accept", "reviewer": "local-user", "reviewed_at": "2026-01-02T03:04:05Z", "replacement_assertion_id": "unexpected", "replacement_assertion": None, "note": None, "schema_version": "1"}
            with self.assertRaisesRegex(ReviewError, "replacement_assertion_id"):
                append_event(directory, base)
            replacement = candidate("ast_replacement_contract")
            replacement["replaces_assertion_id"] = "ast_review_original"
            replacing = dict(base, action="replace", replacement_assertion=replacement, replacement_assertion_id="different-id")
            with self.assertRaisesRegex(ReviewError, "replacement_assertion_id"):
                append_event(directory, replacing)

    def test_load_rejects_sequence_filename_mismatch(self):
        with tempfile.TemporaryDirectory() as root_name:
            directory = Path(root_name)
            event = {"id": "rev_11111111111111111111111111111111", "assertion_id": "ast_review_original", "action": "accept", "reviewer": "local-user", "reviewed_at": "2026-01-02T03:04:05Z", "replacement_assertion_id": None, "replacement_assertion": None, "note": None, "schema_version": "1"}
            append_event(directory, event)
            path = directory / "00000000000000000001.json"
            stored = json.loads(path.read_text())
            stored["sequence"] = 2
            path.write_text(json.dumps(stored), encoding="utf-8")
            with self.assertRaisesRegex(ReviewError, "does not match filename"):
                load_events(directory)

    def test_failed_event_append_does_not_leave_replacement_or_partial_event(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = create_review_project(root)
            with RunningApplication(root) as app:
                page = open_page(app, f"/assertions?project={project}")
                with mock.patch("proofloom.reviews.append_event", side_effect=OSError("disk full")):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        submit_form(app, "/assertions/review", csrf_token=hidden_field(page, "csrf_token"), project=str(project), assertion_id="ast_review_original", action="replace", subject_id="entity_111111111111111111111111", predicate="PRODUCES", object_id="entity_222222222222222222222222")
                self.assertEqual(400, caught.exception.code)
            self.assertFalse((project / ".proofloom" / "assertion-ledger.json").exists())
            events = project / ".proofloom" / "review-events"
            self.assertFalse(events.exists() and list(events.iterdir()))

    def test_concurrent_review_posts_append_every_complete_event(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = create_review_project(root)
            with RunningApplication(root) as app:
                token = hidden_field(open_page(app, f"/assertions?project={project}"), "csrf_token")
                def post(index):
                    return submit_form(app, "/assertions/review", csrf_token=token, project=str(project), assertion_id="ast_review_original", action="accept", note=f"concurrent-{index}").read()
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(post, range(16)))
            event_files = list((project / ".proofloom" / "review-events").glob("*.json"))
            self.assertEqual(16, len(event_files))
            self.assertEqual(16, len([json.loads(path.read_text()) for path in event_files]))

    def test_owner_reviews_candidates_and_restart_preserves_proposal_state_and_history(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = create_review_project(root)
            with RunningApplication(root) as app:
                page = open_page(app, f"/assertions?project={project}")
                self.assertIn("Subject: entity_111111111111111111111111", page)
                self.assertIn("Predicate: VERIFIES", page)
                self.assertIn("Object: entity_222222222222222222222222", page)
                self.assertIn("Primary evidence", page)
                self.assertIn("synthetic-signal.md", page)
                self.assertIn("Signal lesson", page)
                token = hidden_field(page, "csrf_token")
                for action in ("accept", "reject", "needs_domain_review"):
                    page = submit_form(
                        app,
                        "/assertions/review",
                        csrf_token=token,
                        project=str(project),
                        assertion_id="ast_review_original",
                        action=action,
                        note=f"decision {action}",
                    ).read().decode()
                    expected = {"accept": "accepted", "reject": "rejected"}.get(action, action)
                    self.assertIn(f"Current status: {expected}", page)

            original = json.loads(
                (project / ".proofloom" / "candidate-assertions.json").read_text()
            )
            self.assertEqual([candidate()], original)
            self.assertFalse((project / ".proofloom" / "query-graph.json").exists())

            with RunningApplication(root) as restarted:
                page = open_page(restarted, f"/assertions?project={project}")
                self.assertIn("Original extractor proposal", page)
                self.assertIn("Current status: needs_domain_review", page)
                self.assertIn("decision accept", page)
                self.assertIn("decision reject", page)
                self.assertIn("decision needs_domain_review", page)
                self.assertEqual(3, page.count("Reviewer: local-user"))

    def test_replace_rejects_original_and_creates_governed_linked_assertion(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = create_review_project(root)
            with RunningApplication(root) as app:
                page = open_page(app, f"/assertions?project={project}")
                page = submit_form(
                    app,
                    "/assertions/review",
                    csrf_token=hidden_field(page, "csrf_token"),
                    project=str(project),
                    assertion_id="ast_review_original",
                    action="replace",
                    subject_id="entity_111111111111111111111111",
                    predicate="PRODUCES",
                    object_id="entity_222222222222222222222222",
                    note="corrected semantics",
                ).read().decode()
                self.assertIn("Current status: rejected", page)
                self.assertIn("Replaces: ast_review_original", page)
                self.assertIn("Predicate: PRODUCES", page)

            self.assertEqual([candidate()], json.loads((project / ".proofloom" / "candidate-assertions.json").read_text()))
            self.assertFalse((project / ".proofloom" / "assertion-ledger.json").exists())
            event = load_events(project / ".proofloom" / "review-events")[0]
            replacement = event["replacement_assertion"]
            self.assertEqual("ast_review_original", replacement["replaces_assertion_id"])
            self.assertEqual("PRODUCES", replacement["predicate"])
            self.assertEqual(replacement["id"], event["replacement_assertion_id"])

            with RunningApplication(root) as restarted:
                page = open_page(restarted, f"/assertions?project={project}")
                self.assertIn("Terminal: replaced assertions cannot be reviewed again", page)
                with self.assertRaises(urllib.error.HTTPError) as terminal:
                    submit_form(restarted, "/assertions/review", csrf_token=hidden_field(page, "csrf_token"), project=str(project), assertion_id="ast_review_original", action="accept")
                self.assertEqual(409, terminal.exception.code)
                self.assertIn("assertion_id", terminal.exception.read().decode())
                page = submit_form(restarted, "/assertions/review", csrf_token=hidden_field(page, "csrf_token"), project=str(project), assertion_id=replacement["id"], action="accept").read().decode()
                self.assertIn("Current status: accepted", page)

    def test_concurrent_replace_allows_only_one_terminal_transition(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = create_review_project(root)
            with RunningApplication(root) as app:
                token = hidden_field(open_page(app, f"/assertions?project={project}"), "csrf_token")
                def post(predicate):
                    try:
                        submit_form(app, "/assertions/review", csrf_token=token, project=str(project), assertion_id="ast_review_original", action="replace", subject_id="entity_111111111111111111111111", predicate=predicate, object_id="entity_222222222222222222222222").read()
                        return 200
                    except urllib.error.HTTPError as error:
                        return error.code
                with ThreadPoolExecutor(max_workers=2) as pool:
                    statuses = list(pool.map(post, ("PRODUCES", "BLOCKS")))
            self.assertEqual([200, 409], sorted(statuses))
            self.assertEqual(1, len(load_events(project / ".proofloom" / "review-events")))

    def test_invalid_replacement_reports_field_error_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = create_review_project(root)
            with RunningApplication(root) as app:
                page = open_page(app, f"/assertions?project={project}")
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    submit_form(
                        app,
                        "/assertions/review",
                        csrf_token=hidden_field(page, "csrf_token"),
                        project=str(project),
                        assertion_id="ast_review_original",
                        action="replace",
                        subject_id="missing-entity",
                        predicate="BLOCKS",
                        object_id="entity_222222222222222222222222",
                    )
                self.assertEqual(400, caught.exception.code)
                self.assertIn("subject_id", caught.exception.read().decode())
            self.assertFalse((project / ".proofloom" / "assertion-ledger.json").exists())
            self.assertFalse((project / ".proofloom" / "review-events").exists())


if __name__ == "__main__":
    unittest.main()
