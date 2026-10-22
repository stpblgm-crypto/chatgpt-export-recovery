"""Exercise installed console-script dispatch without packaging or a display."""

import contextlib
from importlib.metadata import EntryPoint
import io
from pathlib import Path
import re
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from chatgpt_export_recovery import __main__ as app


class CLIEntryPointTests(unittest.TestCase):
    def installed_cli(self):
        project = (Path(__file__).resolve().parents[1] / 'pyproject.toml').read_text()
        match = re.search(r'^chatgpt-export-recovery-cli = "([^"]+)"$', project, re.MULTILINE)
        self.assertIsNotNone(match, 'The terminal command must remain registered')
        return EntryPoint(name='chatgpt-export-recovery-cli', value=match[1],
                          group='console_scripts').load()

    def test_installed_cli_requires_output_instead_of_opening_gui(self):
        gui = Mock(return_value=99)
        with patch.object(sys, 'argv', ['chatgpt-export-recovery-cli']), \
                patch.dict(sys.modules, {'chatgpt_export_recovery.gui': SimpleNamespace(main=gui)}), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            with self.assertRaises(SystemExit) as raised:
                self.installed_cli()()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('--cli requires --output', errors.getvalue())
        gui.assert_not_called()

    def test_installed_cli_uses_hidden_prompt_contract_without_cli_flag(self):
        with patch.object(sys, 'argv', ['chatgpt-export-recovery-cli', '--output', 'out.zip']), \
                patch('sys.stdin.isatty', return_value=False), \
                patch.object(app.getpass, 'getpass') as prompt, \
                patch.object(app, 'Downloader') as downloader, \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            self.assertEqual(self.installed_cli()(), 2)
        self.assertIn('interactive terminal', errors.getvalue())
        prompt.assert_not_called()
        downloader.assert_not_called()

    def test_cli_wrapper_forwards_explicit_arguments_without_mutating_them(self):
        args = ['--output', 'archive.zip', '--sha256', 'a' * 64]
        original = list(args)
        with patch.object(app, 'main', return_value=17) as main:
            self.assertEqual(app.cli_main(args), 17)
        main.assert_called_once_with(['--cli', *args])
        self.assertEqual(args, original)

    def test_cli_wrapper_uses_process_arguments_when_unspecified(self):
        args = ['chatgpt-export-recovery-cli', '--output', 'archive.zip']
        with patch.object(sys, 'argv', args), patch.object(app, 'main', return_value=19) as main:
            self.assertEqual(app.cli_main(), 19)
            self.assertEqual(sys.argv, args)
        main.assert_called_once_with(['--cli', '--output', 'archive.zip'])

    def test_default_source_entrypoint_still_selects_gui(self):
        gui = Mock(return_value=23)
        with patch.dict(sys.modules, {'chatgpt_export_recovery.gui': SimpleNamespace(main=gui)}):
            self.assertEqual(app.main([]), 23)
        gui.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
