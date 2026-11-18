import unittest

import test_agent


class AgentEditingTests(unittest.TestCase):
    agent = test_agent.AgentInspectionTests.agent

    def test_create_and_patch_node_with_revisions_and_diff(self):
        with self.agent() as (client, project):
            created = client.request('create', 'demo', 'write_file', {
                'path': 'nodes/example/entrypoint.py', 'revision': 'missing',
                'source': 'def example(): return 1\n',
            })
            self.assertTrue(created['ok'], created)
            revision = created['result']['revision']
            self.assertIn('+def example(): return 1', created['result']['diff'])
            patched = client.request('patch', 'demo', 'apply_patch', {
                'path': 'nodes/example/entrypoint.py', 'revision': revision,
                'old_text': 'return 1', 'new_text': 'return 2',
            })
            self.assertTrue(patched['ok'], patched)
            self.assertNotEqual(patched['result']['revision'], revision)
            read = client.request('read', 'demo', 'read_file', {'path': 'nodes/example/entrypoint.py'})['result']
            self.assertEqual(read['content'], 'def example(): return 2\n')
            self.assertEqual(read['revision'], patched['result']['revision'])

    def test_stale_revision_and_denied_edits_preserve_content(self):
        with self.agent() as (client, project):
            revision = client.request('read', 'demo', 'read_file', {'path': 'flow.py'})['result']['revision']
            (project / 'flow.py').write_text('answer = 99\n')
            conflict = client.request('stale', 'demo', 'write_file', {'path': 'flow.py', 'revision': revision, 'source': 'answer = 1\n'})
            self.assertEqual(conflict['error']['code'], 'conflict')
            self.assertEqual(client.request('current', 'demo', 'read_file', {'path': 'flow.py'})['result']['content'], 'answer = 99\n')
            (project / 'link.py').symlink_to(project / 'flow.py')
            for path in ('../escape.py', '.workspace/state.json', '.git/config.json', '__pycache__/file.py', '.venv/test.py', 'link.py', 'binary.exe'):
                with self.subTest(path=path):
                    rejected = client.request('denied', 'demo', 'write_file', {'path': path, 'revision': 'missing', 'source': 'wrong'})
                    self.assertFalse(rejected['ok'], rejected)
            current = client.request('read-current', 'demo', 'read_file', {'path': 'flow.py'})['result']
            for args in ({'path': 'flow.py', 'source': 'wrong'},
                         {'path': 'flow.py', 'revision': 'missing', 'source': 'wrong'}):
                self.assertFalse(client.request('invalid', 'demo', 'write_file', args)['ok'])
            invalid_patch = client.request('no-match', 'demo', 'apply_patch', {'path': 'flow.py', 'revision': current['revision'], 'old_text': 'absent', 'new_text': 'wrong'})
            self.assertEqual(invalid_patch['error']['code'], 'invalid_patch')
            self.assertEqual(client.request('after', 'demo', 'read_file', {'path': 'flow.py'})['result'], current)

    def test_patch_preserves_python_encoding_and_newlines(self):
        with self.agent() as (client, project):
            original = b'# coding: latin-1\r\nname = "caf\xe9"\r\n'
            (project / 'legacy.py').write_bytes(original)
            revision = client.request('read', 'demo', 'read_file', {'path': 'legacy.py'})['result']['revision']
            result = client.request('patch', 'demo', 'apply_patch', {'path': 'legacy.py', 'revision': revision,
                        'old_text': 'caf\u00e9', 'new_text': 'th\u00e9'})
            self.assertTrue(result['ok'], result)
            self.assertEqual((project / 'legacy.py').read_bytes(), b'# coding: latin-1\r\nname = "th\xe9"\r\n')

    def test_invalid_python_encoding_returns_error_without_modifying_source(self):
        with self.agent() as (client, project):
            original = b'# coding: unknown-encoding\nvalue = 1\n'
            (project / 'broken.py').write_bytes(original)
            revision = client.request('read', 'demo', 'read_file', {'path': 'broken.py'})['result']['revision']
            result = client.request('write', 'demo', 'write_file', {'path': 'broken.py', 'revision': revision, 'source': 'value = 2\n'})
            self.assertFalse(result['ok'])
            self.assertEqual((project / 'broken.py').read_bytes(), original)
