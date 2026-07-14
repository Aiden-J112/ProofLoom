import json
import tempfile
import unittest
from pathlib import Path

from proofloom.assertions import write_json_atomic
from proofloom.entities import write_dictionary
from proofloom.graphs import project_query_graph
from proofloom.reviews import (
    current_assertion_status,
    load_events,
    resolve_assertion_evidence,
    review,
)

from test_fixture_extraction import dictionary
from test_project_ui import RunningApplication, hidden_field, open_page, submit_form
from test_source_import_ui import create_project


def assertion(assertion_id: str, primary: str, supporting: list[str] | None = None):
    return {
        "id": assertion_id,
        "subject_id": "entity_111111111111111111111111",
        "predicate": "VERIFIES",
        "object_id": "entity_222222222222222222222222",
        "primary_evidence_id": primary,
        "supporting_evidence_ids": supporting or [],
        "status": "candidate",
        "extraction": {
            "provider": "proofloom",
            "model": "synthetic-invalidation-v1",
            "prompt_version": "1",
            "schema_version": "1",
            "generated_at": "2026-01-02T03:04:05Z",
            "mode": "fixture",
        },
    }


class SourceInvalidationTests(unittest.TestCase):
    def test_normalization_only_rescan_keeps_accepted_assertion_current(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = root / "project"
            project.mkdir()
            source = root / "lesson.md"
            source.write_text("# Lesson\n\nStable evidence.\n", encoding="utf-8")
            with RunningApplication(root) as app:
                page = create_project(app, project)
                fields = {"csrf_token": hidden_field(page, "csrf_token"), "project": str(project), "source": str(source)}
                submit_form(app, "/sources/import", **fields).read()
                metadata = project / ".proofloom"
                first = json.loads((metadata / "source-fragments.json").read_text())
                candidate = assertion("ast_normalized", first[0]["id"])
                write_dictionary(metadata / "entity-dictionary.json", dictionary())
                write_json_atomic(metadata / "candidate-assertions.json", [candidate])
                review("accept", candidate["id"], [candidate], metadata / "review-events", dictionary(), first)

                source.write_bytes(b"# Lesson\r\n\r\nStable evidence.   \r\n")
                submit_form(app, "/sources/import", **fields).read()

            fragments = json.loads((metadata / "source-fragments.json").read_text())
            self.assertEqual(1, len(fragments))
            self.assertEqual(first[0]["content_hash"], fragments[0]["content_hash"])
            self.assertEqual(
                "accepted",
                current_assertion_status(candidate["id"], [candidate], load_events(metadata / "review-events"), fragments),
            )
            self.assertEqual([candidate["id"]], [edge["assertion_id"] for edge in project_query_graph(project)["edges"]])

    def test_heading_and_order_changes_create_changed_history_without_semantic_matching(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = root / "project"
            project.mkdir()
            source = root / "lesson.md"
            source.write_text("# First heading\n\nAlpha.\n\nBeta.\n", encoding="utf-8")
            with RunningApplication(root) as app:
                page = create_project(app, project)
                fields = {"csrf_token": hidden_field(page, "csrf_token"), "project": str(project), "source": str(source)}
                submit_form(app, "/sources/import", **fields).read()
                metadata = project / ".proofloom"
                before = json.loads((metadata / "source-fragments.json").read_text())
                source.write_text("# Renamed heading\n\nBeta.\n\nAlpha.\n", encoding="utf-8")
                submit_form(app, "/sources/import", **fields).read()

            after = json.loads((metadata / "source-fragments.json").read_text())
            self.assertEqual(2, len([item for item in after if item["status"] == "changed"]))
            self.assertEqual(2, len([item for item in after if item["status"] == "current"]))
            self.assertEqual(
                {item["content_hash"] for item in before},
                {item["content_hash"] for item in after if item["status"] == "current"},
            )

    def test_reverting_content_reactivates_one_content_addressed_record(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = root / "project"
            project.mkdir()
            source = root / "lesson.md"
            source.write_text("# Lesson\n\nVersion one.\n", encoding="utf-8")
            with RunningApplication(root) as app:
                page = create_project(app, project)
                fields = {"csrf_token": hidden_field(page, "csrf_token"), "project": str(project), "source": str(source)}
                submit_form(app, "/sources/import", **fields).read()
                metadata = project / ".proofloom"
                original_id = json.loads((metadata / "source-fragments.json").read_text())[0]["id"]
                source.write_text("# Lesson\n\nVersion two.\n", encoding="utf-8")
                submit_form(app, "/sources/import", **fields).read()
                source.write_text("# Lesson\n\nVersion one.\n", encoding="utf-8")
                submit_form(app, "/sources/import", **fields).read()

            fragments = json.loads((metadata / "source-fragments.json").read_text())
            self.assertEqual(len(fragments), len({item["id"] for item in fragments}))
            restored = [item for item in fragments if item["id"] == original_id]
            self.assertEqual(1, len(restored))
            self.assertEqual("current", restored[0]["status"])

    def test_rescan_marks_changed_primary_evidence_stale_and_withdraws_projection(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = root / "project"
            project.mkdir()
            source = root / "lesson.md"
            source.write_text("# Lesson\n\nThe Inspector verifies the Report.\n", encoding="utf-8")

            with RunningApplication(root) as app:
                page = create_project(app, project)
                fields = {
                    "csrf_token": hidden_field(page, "csrf_token"),
                    "project": str(project),
                    "source": str(source),
                }
                submit_form(app, "/sources/import", **fields).read()
                metadata = project / ".proofloom"
                original_fragment = json.loads(
                    (metadata / "source-fragments.json").read_text(encoding="utf-8")
                )[0]
                candidate = assertion("ast_source_change", original_fragment["id"])
                write_dictionary(metadata / "entity-dictionary.json", dictionary())
                write_json_atomic(metadata / "candidate-assertions.json", [candidate])
                review(
                    "accept",
                    candidate["id"],
                    [candidate],
                    metadata / "review-events",
                    dictionary(),
                    [original_fragment],
                )
                self.assertEqual(1, len(project_query_graph(project)["edges"]))

                source.write_text("# Lesson\n\nThe Inspector audits the Report.\n", encoding="utf-8")
                rescanned_page = submit_form(app, "/sources/import", **fields).read().decode()
                self.assertIn("Source Fragment status: changed", rescanned_page)
                self.assertIn("Source Fragment status: current", rescanned_page)
                assertion_page = open_page(app, f"/assertions?project={project}")
                self.assertIn("Current status: stale", assertion_page)
                self.assertIn("Action: accept", assertion_page)

            fragments = json.loads(
                (metadata / "source-fragments.json").read_text(encoding="utf-8")
            )
            events = load_events(metadata / "review-events")
            self.assertEqual("changed", next(f for f in fragments if f["id"] == original_fragment["id"])["status"])
            self.assertEqual(1, len([f for f in fragments if f["status"] == "current"]))
            self.assertEqual(
                "stale",
                current_assertion_status(candidate["id"], [candidate], events, fragments),
            )
            self.assertEqual([], project_query_graph(project)["edges"])
            self.assertEqual([candidate], json.loads((metadata / "candidate-assertions.json").read_text()))
            self.assertEqual(1, len(events))

    def test_changed_supporting_evidence_stales_assertion_but_remains_traceable(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = root / "project"
            project.mkdir()
            source = root / "lesson.md"
            source.write_text("# Lesson\n\nPrimary passage.\n\nSupporting passage.\n", encoding="utf-8")

            with RunningApplication(root) as app:
                page = create_project(app, project)
                fields = {"csrf_token": hidden_field(page, "csrf_token"), "project": str(project), "source": str(source)}
                submit_form(app, "/sources/import", **fields).read()
                metadata = project / ".proofloom"
                original = json.loads((metadata / "source-fragments.json").read_text())
                candidate = assertion("ast_support_change", original[0]["id"], [original[1]["id"]])
                write_dictionary(metadata / "entity-dictionary.json", dictionary())
                write_json_atomic(metadata / "candidate-assertions.json", [candidate])
                review("accept", candidate["id"], [candidate], metadata / "review-events", dictionary(), original)

                source.write_text("# Lesson\n\nPrimary passage.\n\nRevised supporting passage.\n", encoding="utf-8")
                submit_form(app, "/sources/import", **fields).read()

            fragments = json.loads((metadata / "source-fragments.json").read_text())
            events = load_events(metadata / "review-events")
            self.assertEqual("stale", current_assertion_status(candidate["id"], [candidate], events, fragments))
            _, evidence = resolve_assertion_evidence(candidate["id"], [candidate], events, fragments)
            self.assertEqual(["Primary passage.", "Supporting passage."], [item["content"] for item in evidence])
            self.assertEqual([], project_query_graph(project)["edges"])

    def test_ui_reextraction_preserves_original_and_requires_fresh_review(self):
        class ReusingIdExtractor:
            def extract(self, entity_dictionary, source_fragments):
                current = next(fragment for fragment in source_fragments if fragment.get("status") == "current")
                return [assertion("ast_reused_by_extractor", str(current["id"]))]

        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = root / "project"
            project.mkdir()
            source = root / "lesson.md"
            source.write_text("# Lesson\n\nOriginal evidence.\n", encoding="utf-8")
            runner = RunningApplication(root)
            runner.server.fixture_extractor = ReusingIdExtractor()
            with runner as app:
                page = create_project(app, project)
                fields = {"csrf_token": hidden_field(page, "csrf_token"), "project": str(project), "source": str(source)}
                submit_form(app, "/sources/import", **fields).read()
                metadata = project / ".proofloom"
                original_fragment = json.loads((metadata / "source-fragments.json").read_text())[0]
                original = assertion("ast_reused_by_extractor", original_fragment["id"])
                write_dictionary(metadata / "entity-dictionary.json", dictionary())
                write_json_atomic(metadata / "candidate-assertions.json", [original])
                review("accept", original["id"], [original], metadata / "review-events", dictionary(), [original_fragment])

                source.write_text("# Lesson\n\nRe-extracted evidence.\n", encoding="utf-8")
                submit_form(app, "/sources/import", **fields).read()
                assertions_page = open_page(app, f"/assertions?project={project}")
                submit_form(
                    app,
                    "/assertions/extract-fixture",
                    csrf_token=hidden_field(assertions_page, "csrf_token"),
                    project=str(project),
                ).read()
                assertions_page = open_page(app, f"/assertions?project={project}")
                submit_form(
                    app,
                    "/assertions/extract-fixture",
                    csrf_token=hidden_field(assertions_page, "csrf_token"),
                    project=str(project),
                ).read()

            candidates = json.loads((metadata / "candidate-assertions.json").read_text())
            events = load_events(metadata / "review-events")
            self.assertEqual(2, len(candidates))
            self.assertEqual(original, candidates[0])
            reextracted = candidates[1]
            self.assertNotEqual(original["id"], reextracted["id"])
            self.assertEqual("candidate", current_assertion_status(reextracted["id"], candidates, events, json.loads((metadata / "source-fragments.json").read_text())))
            self.assertEqual([], project_query_graph(project)["edges"])

            fragments = json.loads((metadata / "source-fragments.json").read_text())
            review("accept", reextracted["id"], candidates, metadata / "review-events", dictionary(), fragments)
            self.assertEqual([reextracted["id"]], [edge["assertion_id"] for edge in project_query_graph(project)["edges"]])


if __name__ == "__main__":
    unittest.main()
