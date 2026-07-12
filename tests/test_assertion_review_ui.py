import json
import tempfile
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from unittest import mock
from pathlib import Path

from proofloom.assertions import write_json_atomic
from proofloom.entities import write_dictionary

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
            ledger = json.loads((project / ".proofloom" / "assertion-ledger.json").read_text())
            replacement = next(item for item in ledger if item.get("replaces_assertion_id"))
            self.assertEqual("ast_review_original", replacement["replaces_assertion_id"])
            self.assertEqual("PRODUCES", replacement["predicate"])

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
