"""Inspect an MCP-completed run and repaired source in the real Workspace UI."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', required=True)
parser.add_argument('--run-id', required=True)
parser.add_argument('--source-version', required=True)
parser.add_argument('--output', required=True, type=Path)
parser.add_argument('--chromium', help='Optional existing Chromium executable')
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, **({'executable_path': args.chromium} if args.chromium else {}))
    page = browser.new_page(viewport={'width': 1440, 'height': 1000})
    page.goto(args.url)
    frame = page.frame_locator('iframe')
    expect(frame.locator('#status')).to_have_text('Succeeded')
    frame.locator('.history-run').first.click()
    expect(frame.locator('#history-detail')).to_contain_text(args.run_id)
    expect(frame.locator('#history-detail')).to_contain_text(args.source_version)
    history = frame.locator('#history-detail').inner_text()
    frame.locator('#history-detail').scroll_into_view_if_needed()
    page.screenshot(path=str(args.output / 'ui-history.png'))
    frame.get_by_role('button', name='View files', exact=True).click()
    expect(frame.locator('#node-source-input')).to_have_value('def work():\n    return 42\n')
    source = frame.locator('#node-source-input').input_value()
    page.screenshot(path=str(args.output / 'ui-source.png'))
    (args.output / 'ui-observation.json').write_text(json.dumps({
        'url': args.url, 'history_text': history, 'source': source}, indent=2))
    print(json.dumps({'run_id': args.run_id, 'source_version': args.source_version, 'source': source}))
    browser.close()
