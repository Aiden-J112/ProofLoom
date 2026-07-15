import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from proofloom.assertions import write_json_atomic
from proofloom.graphs import project_query_graph
from proofloom.release import ReleaseIntegrityError, check_project_integrity
from proofloom.reviews import review
from proofloom.sources import parse_markdown

from test_assertion_review_ui import candidate, create_review_project
from test_fixture_extraction import dictionary


def release_fragments(status: str = "current") -> list[dict[str, object]]:
    fragment = parse_markdown(
        "# Signal lesson\n\nThe Inspector verifies the generated Report.\n",
        "synthetic-signal.md",
    )[0]
    fragment["status"] = status
    return [fragment]


def release_candidate(assertion_id: str = "ast_review_original") -> dict[str, object]:
    return dict(
        candidate(assertion_id),
        primary_evidence_id=release_fragments()[0]["id"],
    )


class ReleaseIntegrityTests(unittest.TestCase):
    def test_public_check_command_verifies_every_persisted_edge(self):
        with tempfile.TemporaryDirectory() as root_name:
            project = create_review_project(Path(root_name))
            metadata = project / ".proofloom"
            assertion = release_candidate()
            source_fragments = release_fragments()
            write_json_atomic(metadata / "candidate-assertions.json", [assertion])
            write_json_atomic(metadata / "source-fragments.json", source_fragments)
            review(
                "accept",
                "ast_review_original",
                [assertion],
                metadata / "review-events",
                dictionary(),
                source_fragments,
            )
            project_query_graph(project)

            result = subprocess.run(
                [sys.executable, "-m", "proofloom.app", "check", str(project)],
                cwd=Path(__file__).parents[1],
                env={**os.environ, "PYTHONPATH": "src"},
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("Release integrity passed: 1 graph edge", result.stdout)
            self.assertEqual(1, check_project_integrity(project)["checked_edges"])

    def test_check_rejects_edges_not_backed_by_current_accepted_located_assertions(self):
        cases = {
            "rejected": ("reject", release_fragments()),
            "needs_domain_review": ("needs_domain_review", release_fragments()),
            "stale": ("accept", release_fragments("changed")),
            "missing evidence": ("accept", release_fragments()),
        }
        for label, (action, source_fragments) in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as root_name:
                project = create_review_project(Path(root_name))
                metadata = project / ".proofloom"
                assertion = release_candidate(f"ast_{label.replace(' ', '_')}")
                if label == "missing evidence":
                    assertion["primary_evidence_id"] = "src_missing"
                write_json_atomic(metadata / "candidate-assertions.json", [assertion])
                write_json_atomic(metadata / "source-fragments.json", source_fragments)
                review(
                    action,
                    str(assertion["id"]),
                    [assertion],
                    metadata / "review-events",
                    dictionary(),
                    source_fragments,
                )
                self._write_tampered_graph(metadata, assertion)

                with self.assertRaisesRegex(ReleaseIntegrityError, label):
                    check_project_integrity(project)

    def test_check_rejects_an_edge_for_a_replaced_assertion(self):
        with tempfile.TemporaryDirectory() as root_name:
            project = create_review_project(Path(root_name))
            metadata = project / ".proofloom"
            original = release_candidate()
            source_fragments = release_fragments()
            write_json_atomic(metadata / "candidate-assertions.json", [original])
            write_json_atomic(metadata / "source-fragments.json", source_fragments)
            review(
                "replace",
                str(original["id"]),
                [original],
                metadata / "review-events",
                dictionary(),
                source_fragments,
                {
                    "subject_id": str(original["subject_id"]),
                    "predicate": "PRODUCES",
                    "object_id": str(original["object_id"]),
                },
            )
            self._write_tampered_graph(metadata, original)

            with self.assertRaisesRegex(ReleaseIntegrityError, "replaced"):
                check_project_integrity(project)

    def test_check_rejects_a_malformed_source_fragment_record(self):
        with tempfile.TemporaryDirectory() as root_name:
            project = create_review_project(Path(root_name))
            metadata = project / ".proofloom"
            assertion = release_candidate()
            malformed = release_fragments("retired")
            write_json_atomic(metadata / "candidate-assertions.json", [assertion])
            write_json_atomic(metadata / "source-fragments.json", malformed)
            review(
                "accept",
                str(assertion["id"]),
                [assertion],
                metadata / "review-events",
                dictionary(),
                malformed,
            )
            self._write_tampered_graph(metadata, assertion)

            with self.assertRaisesRegex(ReleaseIntegrityError, "Source Fragment"):
                check_project_integrity(project)

    @staticmethod
    def _write_tampered_graph(metadata: Path, assertion: dict[str, object]) -> None:
        write_json_atomic(
            metadata / "query-graph.json",
            {
                "schema_version": "1",
                "nodes": [],
                "edges": [
                    {
                        "source": assertion["subject_id"],
                        "type": assertion["predicate"],
                        "target": assertion["object_id"],
                        "assertion_id": assertion["id"],
                    }
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
