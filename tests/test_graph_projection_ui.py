import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from jsonschema import Draft202012Validator

from proofloom.assertions import write_json_atomic
from proofloom.entities import write_dictionary
from proofloom.graphs import project_query_graph
from proofloom.reviews import fold_status, load_events, review

from test_assertion_review_ui import candidate, create_review_project
from test_fixture_extraction import dictionary, fragments
from test_project_ui import RunningApplication, hidden_field, link_from, open_page, submit_form


class GraphProjectionUiTests(unittest.TestCase):
    def test_owner_projects_an_accepted_assertion_to_a_deterministic_schema_valid_graph(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = create_review_project(root)
            metadata = project / ".proofloom"
            entity_dictionary = dictionary()
            entity_dictionary["candidates"] = [
                {"id": "candidate_333333333333333333333333", "name": "Unreviewed", "status": "candidate"}
            ]
            write_dictionary(metadata / "entity-dictionary.json", entity_dictionary)
            review(
                "accept",
                "ast_review_original",
                [candidate()],
                metadata / "review-events",
                entity_dictionary,
                fragments(),
            )

            first = project_query_graph(project)
            persisted_once = (metadata / "query-graph.json").read_bytes()
            second = project_query_graph(project)

            self.assertEqual(first, second)
            self.assertEqual(persisted_once, (metadata / "query-graph.json").read_bytes())
            self.assertEqual(
                [{
                    "source": "entity_111111111111111111111111",
                    "type": "VERIFIES",
                    "target": "entity_222222222222222222222222",
                    "assertion_id": "ast_review_original",
                }],
                first["edges"],
            )
            self.assertEqual(
                [
                    {"id": "entity_111111111111111111111111", "type": "Component", "name": "Inspector"},
                    {"id": "entity_222222222222222222222222", "type": "Artifact", "name": "Report"},
                ],
                first["nodes"],
            )
            self.assertNotIn("Unreviewed", json.dumps(first))
            schema_path = Path(__file__).parents[1] / "src" / "proofloom" / "schemas" / "query-graph.schema.json"
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            Draft202012Validator(schema).validate(first)

    def test_projection_uses_current_review_state_and_revalidates_evidence(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = create_review_project(root)
            metadata = project / ".proofloom"
            candidates = [
                candidate("ast_accepted"),
                candidate("ast_rejected"),
                candidate("ast_domain_review"),
                candidate("ast_unreviewed"),
                candidate("ast_replaced"),
                dict(candidate("ast_invalid_evidence"), primary_evidence_id="missing"),
                dict(candidate("ast_untraceable_evidence"), primary_evidence_id="src_untraceable"),
            ]
            write_json_atomic(metadata / "candidate-assertions.json", candidates)
            source_fragments = [*fragments(), {"id": "src_untraceable"}]
            write_json_atomic(metadata / "source-fragments.json", source_fragments)
            events_path = metadata / "review-events"
            for action, assertion_id in (
                ("accept", "ast_accepted"),
                ("reject", "ast_rejected"),
                ("needs_domain_review", "ast_domain_review"),
                ("accept", "ast_invalid_evidence"),
                ("accept", "ast_untraceable_evidence"),
            ):
                review(action, assertion_id, candidates, events_path, dictionary(), source_fragments)
            replacement_event = review(
                "replace",
                "ast_replaced",
                candidates,
                events_path,
                dictionary(),
                source_fragments,
                {"subject_id": "entity_111111111111111111111111", "predicate": "PRODUCES", "object_id": "entity_222222222222222222222222"},
            )
            replacement_id = replacement_event["replacement_assertion_id"]
            review(
                "accept",
                str(replacement_id),
                candidates,
                events_path,
                dictionary(),
                source_fragments,
            )

            graph = project_query_graph(project)

            self.assertEqual(
                {"ast_accepted", replacement_id},
                {edge["assertion_id"] for edge in graph["edges"]},
            )
            self.assertNotIn("ast_invalid_evidence", json.dumps(graph))
            self.assertNotIn("ast_untraceable_evidence", json.dumps(graph))
            self.assertEqual("rejected", fold_status("ast_replaced", load_events(events_path)))

    def test_owner_filters_graph_types_and_traces_an_edge_to_original_evidence(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            project = create_review_project(root)
            metadata = project / ".proofloom"
            reviewed = candidate()
            reviewed["supporting_evidence_ids"] = ["src_supporting"]
            source_fragments = [
                *fragments(),
                {
                    "id": "src_supporting",
                    "source_file": "supporting-signal.md",
                    "heading_path": ["Supporting lesson"],
                    "ordinal": 1,
                    "kind": "paragraph",
                    "content": "A supporting synthetic passage.",
                    "content_hash": "sha256:supporting",
                    "schema_version": "1",
                },
            ]
            write_json_atomic(metadata / "candidate-assertions.json", [reviewed])
            write_json_atomic(metadata / "source-fragments.json", source_fragments)
            review(
                "accept",
                "ast_review_original",
                [reviewed],
                metadata / "review-events",
                dictionary(),
                source_fragments,
            )
            with RunningApplication(root) as app:
                assertions_page = open_page(
                    app,
                    f"/assertions?project={urllib.parse.quote(str(project))}",
                )
                self.assertLess(
                    assertions_page.index("The Inspector verifies the generated Report."),
                    assertions_page.index("A supporting synthetic passage."),
                )
                graph_page = submit_form(
                    app,
                    "/graphs/project",
                    csrf_token=hidden_field(assertions_page, "csrf_token"),
                    project=str(project),
                ).read().decode()
                self.assertIn("Graph Explorer", graph_page)
                self.assertIn("Entity type: Component", graph_page)
                self.assertIn("Entity type: Artifact", graph_page)
                self.assertIn("Relationship type: VERIFIES", graph_page)

                evidence_page = open_page(
                    app,
                    link_from(graph_page, "Trace evidence for ast_review_original"),
                )
                self.assertIn("Assertion status: accepted", evidence_page)
                self.assertIn("Source file: synthetic-signal.md", evidence_page)
                self.assertIn("Heading path: Signal lesson", evidence_page)
                self.assertIn("The Inspector verifies the generated Report.", evidence_page)
                self.assertIn("Source file: supporting-signal.md", evidence_page)
                self.assertLess(
                    evidence_page.index("The Inspector verifies the generated Report."),
                    evidence_page.index("A supporting synthetic passage."),
                )

                component_only = open_page(
                    app,
                    f"/graph?project={urllib.parse.quote(str(project))}&entity_type=Component",
                )
                self.assertIn("Inspector", component_only)
                self.assertNotIn("Entity type: Artifact", component_only)
                self.assertNotIn("Trace evidence for ast_review_original", component_only)

                no_matching_relationship = open_page(
                    app,
                    f"/graph?project={urllib.parse.quote(str(project))}&relationship_type=BLOCKS",
                )
                self.assertNotIn("Trace evidence for ast_review_original", no_matching_relationship)


if __name__ == "__main__":
    unittest.main()
