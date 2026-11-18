import unittest

from playwright.sync_api import expect

from browser_support import browser_page, local_ui
from test_auto_reload import FLOW, sheet_view, write_sheet
import test_agent


class AgentEditUITests(unittest.TestCase):
    agent = test_agent.AgentInspectionTests.agent

    def save(self, client, path, source):
        revision = client.request('read-' + path, 'demo', 'read_file', {'path': path})['result']['revision']
        result = client.request('write-' + path, 'demo', 'write_file', {'path': path, 'revision': revision, 'source': source})
        self.assertTrue(result['ok'], result)

    def test_agent_edit_reloads_graph_and_recovers_validation_failure(self):
        with self.agent() as (client, project), browser_page() as page:
            write_sheet(project)
            with local_ui(project) as (url, _):
                page.goto(url)
                frame, view = sheet_view(page)
                expect(view.get_by_role('button', name='Inspect Original')).to_be_visible()
                self.save(client, 'flow.py', FLOW.replace('Original', 'Updated'))
                expect(view.get_by_role('button', name='Inspect Updated')).to_be_visible()
                self.save(client, 'helper.py', 'def broken(\n')
                expect(page.locator('#notice-message')).to_contain_text('Syntax:')
                self.save(client, 'helper.py', 'def value(): return "agent recovered"\n')
                expect(frame).to_have_attribute('src', '/flows/0/revision/3/view/')
                expect(view.get_by_role('button', name='Inspect Updated')).to_be_visible()
                view.get_by_role('button', name='Run flow').click()
                expect(view.locator('#result pre')).to_have_text('"agent recovered"')
                view.get_by_role('button', name='Inspect Updated').click()
                from browser_support import show_panel
                show_panel(view, 'Inspector')
                view.get_by_role('button', name='View files').click()
                expect(view.get_by_role('textbox', name='Source code')).to_have_value('def value(): return "agent recovered"\n')

    def test_agent_edit_preserves_unsaved_browser_draft(self):
        with self.agent() as (client, project), browser_page() as page:
            write_sheet(project)
            with local_ui(project) as (url, _):
                page.goto(url)
                frame, view = sheet_view(page)
                expect(view.get_by_role('button', name='Inspect Original')).to_be_visible()
                page.get_by_role('button', name='Edit flow.py').click()
                dialog = page.get_by_role('dialog', name='Edit flow.py')
                editor = dialog.get_by_role('textbox', name='Source code')
                draft = FLOW + '# unsaved browser work\n'
                editor.fill(draft)
                self.save(client, 'helper.py', 'def value(): return "agent edit"\n')
                expect(dialog.locator('#source-change-message')).to_be_visible()
                expect(editor).to_have_value(draft)
                expect(frame).to_have_attribute('src', '/flows/0/view/')
                dialog.get_by_role('button', name='Save', exact=True).click()
                expect(dialog.get_by_role('status')).to_contain_text('Saved.')
                expect(frame).to_have_attribute('src', '/flows/0/revision/1/view/')
                self.assertEqual(client.request('read-draft', 'demo', 'read_file', {'path': 'flow.py'})['result']['content'], draft)

    def test_agent_edit_waits_for_active_run_before_reloading(self):
        with self.agent() as (client, project), browser_page() as page:
            write_sheet(project)
            (project / 'helper.py').write_text('from pathlib import Path\nfrom time import sleep\ndef value():\n    while not Path("release").exists(): sleep(.02)\n    return "original"\n')
            with local_ui(project) as (url, _):
                page.goto(url)
                frame, view = sheet_view(page)
                view.get_by_role('button', name='Run flow').click()
                expect(page.get_by_role('tab', name='Main')).to_contain_text('Running')
                self.save(client, 'helper.py', 'def value(): return "next run"\n')
                expect(page.locator('#reload-message')).to_have_text('Source changed. Reload pending.')
                expect(frame).to_have_attribute('src', '/flows/0/view/')
                (project / 'release').touch()
                expect(frame).to_have_attribute('src', '/flows/0/revision/1/view/')
                runs = page.request.get(url + '/flows/0/revision/1/view/history').json()
                self.assertEqual([run['status'] for run in runs], ['succeeded'])
                view.get_by_role('button', name='Run flow').click()
                expect(view.locator('#result pre')).to_have_text('"next run"')
