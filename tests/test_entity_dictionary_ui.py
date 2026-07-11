import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from test_project_ui import RunningApplication, hidden_field, link_from, open_page, submit_form
from test_source_import_ui import create_project


class EntityDictionaryUiTests(unittest.TestCase):
    def test_unknown_name_is_candidate_until_reviewed_with_controlled_type(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            project_path = root_path / "project"
            project_path.mkdir()
            with RunningApplication(root_path) as app:
                project = create_project(app, project_path)
                dictionary = open_page(app, link_from(project, "Review Entity Dictionary"))
                candidate_page = submit_form(
                    app,
                    "/entities/candidates",
                    csrf_token=hidden_field(dictionary, "csrf_token"),
                    project=str(project_path),
                    name="Signal Router",
                ).read().decode()
                self.assertIn("Candidate entities", candidate_page)
                self.assertIn("Signal Router", candidate_page)
                self.assertNotIn("Accepted entities</h3><article", candidate_page)

                candidate_id = hidden_field(candidate_page, "candidate_id")
                accepted_page = submit_form(
                    app,
                    "/entities/accept",
                    csrf_token=hidden_field(candidate_page, "csrf_token"),
                    project=str(project_path),
                    candidate_id=candidate_id,
                    entity_type="Component",
                ).read().decode()
                self.assertIn("Status: accepted", accepted_page)
                self.assertIn("Type: Component", accepted_page)

            with RunningApplication(root_path) as restarted_app:
                reopened = open_page(
                    restarted_app,
                    f"/entities?project={urllib.parse.quote(str(project_path))}",
                )
                self.assertIn("Signal Router", reopened)
                self.assertIn("Status: accepted", reopened)
                self.assertIn("No candidate entities", reopened)

    def test_display_name_and_aliases_can_change_without_changing_entity_id(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            project_path = root_path / "project"
            project_path.mkdir()
            with RunningApplication(root_path) as app:
                page = create_project(app, project_path)
                page = open_page(app, link_from(page, "Review Entity Dictionary"))
                page = submit_form(
                    app, "/entities/candidates", csrf_token=hidden_field(page, "csrf_token"),
                    project=str(project_path), name="Routing Unit"
                ).read().decode()
                page = submit_form(
                    app, "/entities/accept", csrf_token=hidden_field(page, "csrf_token"),
                    project=str(project_path), candidate_id=hidden_field(page, "candidate_id"),
                    entity_type="Pattern"
                ).read().decode()
                entity_id = hidden_field(page, "entity_id")
                page = submit_form(
                    app, "/entities/update", csrf_token=hidden_field(page, "csrf_token"),
                    project=str(project_path), entity_id=entity_id,
                    display_name="Message Router", aliases="routing unit, dispatcher"
                ).read().decode()
                self.assertIn(entity_id, page)
                self.assertIn("Message Router", page)
                self.assertIn("routing unit, dispatcher", page)

            with RunningApplication(root_path) as restarted_app:
                reopened = open_page(
                    restarted_app,
                    f"/entities?project={urllib.parse.quote(str(project_path))}",
                )
                self.assertIn(entity_id, reopened)
                self.assertIn("Message Router", reopened)
                self.assertIn("routing unit, dispatcher", reopened)

    def test_invalid_type_and_ambiguous_alias_are_rejected_without_partial_write(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            project_path = root_path / "project"
            project_path.mkdir()
            with RunningApplication(root_path) as app:
                page = create_project(app, project_path)
                page = open_page(app, link_from(page, "Review Entity Dictionary"))
                token = hidden_field(page, "csrf_token")
                page = submit_form(app, "/entities/candidates", csrf_token=token,
                                   project=str(project_path), name="First").read().decode()
                with self.assertRaises(urllib.error.HTTPError) as invalid:
                    submit_form(app, "/entities/accept", csrf_token=token,
                                project=str(project_path), candidate_id=hidden_field(page, "candidate_id"),
                                entity_type="Person")
                self.assertEqual(400, invalid.exception.code)
                self.assertIn("Component, Artifact, Pattern, or Concept", invalid.exception.read().decode())

                page = submit_form(app, "/entities/accept", csrf_token=token,
                                   project=str(project_path), candidate_id=hidden_field(page, "candidate_id"),
                                   entity_type="Concept").read().decode()
                first_id = hidden_field(page, "entity_id")
                page = submit_form(app, "/entities/update", csrf_token=token,
                                   project=str(project_path), entity_id=first_id,
                                   display_name="First", aliases="shared name").read().decode()
                page = submit_form(app, "/entities/candidates", csrf_token=token,
                                   project=str(project_path), name="Second").read().decode()
                page = submit_form(app, "/entities/accept", csrf_token=token,
                                   project=str(project_path), candidate_id=hidden_field(page, "candidate_id"),
                                   entity_type="Artifact").read().decode()
                second_id = [value for value in _hidden_fields(page, "entity_id") if value != first_id][0]
                with self.assertRaises(urllib.error.HTTPError) as conflict:
                    submit_form(app, "/entities/update", csrf_token=token,
                                project=str(project_path), entity_id=second_id,
                                display_name="Second", aliases="shared name")
                self.assertEqual(409, conflict.exception.code)
                self.assertIn("already belongs to", conflict.exception.read().decode())

                unchanged = open_page(
                    app, f"/entities?project={urllib.parse.quote(str(project_path))}"
                )
                self.assertNotIn('value="shared name"', unchanged.split(second_id, 1)[1])

    def test_entity_write_requires_csrf(self):
        with tempfile.TemporaryDirectory() as root:
            project_path = Path(root) / "project"
            project_path.mkdir()
            with RunningApplication(Path(root)) as app:
                create_project(app, project_path)
                with self.assertRaises(urllib.error.HTTPError) as error:
                    submit_form(app, "/entities/candidates", project=str(project_path), name="Unsafe")
                self.assertEqual(403, error.exception.code)


def _hidden_fields(page: str, name: str) -> list[str]:
    import html
    import re
    return [html.unescape(value) for value in re.findall(
        rf'<input type="hidden" name="{re.escape(name)}" value="([^"]+)">', page
    )]


if __name__ == "__main__":
    unittest.main()
