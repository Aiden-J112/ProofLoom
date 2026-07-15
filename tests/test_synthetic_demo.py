import json
import shutil
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from test_project_ui import (
    RunningApplication,
    hidden_field,
    link_from,
    open_page,
    submit_form,
)


EXAMPLE_SOURCES = Path(__file__).parents[1] / "examples" / "synthetic-workflow"


class SyntheticDemoTests(unittest.TestCase):
    def test_owner_learner_completes_two_document_build_to_explore_workflow(self):
        with tempfile.TemporaryDirectory() as root_name:
            root = Path(root_name)
            sources = root / "examples" / "synthetic-workflow"
            sources.parent.mkdir()
            shutil.copytree(EXAMPLE_SOURCES, sources)
            project = root / "project"
            project.mkdir()

            with RunningApplication(root) as app:
                home = open_page(app, "/")
                project_page = submit_form(
                    app,
                    "/projects/create",
                    csrf_token=hidden_field(home, "csrf_token"),
                    directory=str(project),
                    name="Synthetic Workflow",
                ).read().decode()
                project_page = submit_form(
                    app,
                    "/sources/import",
                    csrf_token=hidden_field(project_page, "csrf_token"),
                    project=str(project),
                    source=str(sources),
                ).read().decode()

                dictionary_page = open_page(
                    app, link_from(project_page, "Review Entity Dictionary")
                )
                for name, entity_type in (
                    ("Inspector", "Component"),
                    ("Inspection Report", "Artifact"),
                    ("Safety Gate", "Component"),
                    ("Risky Command", "Artifact"),
                ):
                    dictionary_page = submit_form(
                        app,
                        "/entities/candidates",
                        csrf_token=hidden_field(dictionary_page, "csrf_token"),
                        project=str(project),
                        name=name,
                    ).read().decode()
                    dictionary_page = submit_form(
                        app,
                        "/entities/accept",
                        csrf_token=hidden_field(dictionary_page, "csrf_token"),
                        project=str(project),
                        candidate_id=hidden_field(dictionary_page, "candidate_id"),
                        entity_type=entity_type,
                    ).read().decode()

                assertion_page = open_page(
                    app,
                    f"/assertions?project={urllib.parse.quote(str(project))}",
                )
                assertion_page = submit_form(
                    app,
                    "/assertions/extract-fixture",
                    csrf_token=hidden_field(assertion_page, "csrf_token"),
                    project=str(project),
                ).read().decode()

                metadata = project / ".proofloom"
                candidates = json.loads(
                    (metadata / "candidate-assertions.json").read_text(encoding="utf-8")
                )
                validation = json.loads(
                    (metadata / "assertion-validation.json").read_text(encoding="utf-8")
                )
                self.assertEqual(2, len(candidates))
                self.assertEqual([True, True], [item["valid"] for item in validation])

                for candidate in candidates:
                    assertion_page = submit_form(
                        app,
                        "/assertions/review",
                        csrf_token=hidden_field(assertion_page, "csrf_token"),
                        project=str(project),
                        assertion_id=str(candidate["id"]),
                        action="accept",
                    ).read().decode()

                graph_page = submit_form(
                    app,
                    "/graphs/project",
                    csrf_token=hidden_field(assertion_page, "csrf_token"),
                    project=str(project),
                ).read().decode()
                self.assertEqual(2, graph_page.count('class="graph-edge"'))
                for candidate in candidates:
                    evidence_page = open_page(
                        app,
                        link_from(
                            graph_page,
                            f"Trace evidence for {candidate['id']}",
                        ),
                    )
                    self.assertIn("Assertion status: accepted", evidence_page)
                    self.assertIn(
                        "Source file: examples/synthetic-workflow/", evidence_page
                    )

            source_files = {
                fragment["source_file"]
                for fragment in json.loads(
                    (metadata / "source-fragments.json").read_text(encoding="utf-8")
                )
            }
            self.assertEqual(
                {
                    "examples/synthetic-workflow/inspection.md",
                    "examples/synthetic-workflow/safety.md",
                },
                source_files,
            )


if __name__ == "__main__":
    unittest.main()
