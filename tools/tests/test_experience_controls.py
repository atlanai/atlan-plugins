"""DOM-event logic tests for the generated snapshot, not browser/layout verification.

The script and element text come from the public HTML renderer. Node's vm executes
that script against only the DOM operations these controls need; no network,
clipboard service, browser or third-party DOM dependency is involved.
"""

from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parents[2] / "plugins/atlanai-analytics/skills/atlan-analytics/scripts"
sys.path.insert(0, str(SCRIPTS))
import experience


class GeneratedDOM(HTMLParser):
    """Retain real rendered text/attributes rather than duplicating them in the JS fake."""

    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, page):
        super().__init__(convert_charrefs=True)
        self.root = {"tag": "root", "attrs": {}, "children": []}
        self.stack = [self.root]
        self.elements = []
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        node = {"tag": tag, "attrs": dict(attrs), "children": []}
        self.stack[-1]["children"].append(node)
        self.elements.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        self.stack[-1]["children"].append(value)

    @classmethod
    def text(cls, node):
        return "".join(child if isinstance(child, str) else cls.text(child) for child in node["children"])

    @classmethod
    def spec(cls, node):
        text = cls.text(node)
        return {"tag": node["tag"], "attrs": node["attrs"], "textContent": text,
                "value": text if node["tag"] == "textarea" else node["attrs"].get("value", "")}

    def payload(self):
        scripts = [self.text(node) for node in self.elements if node["tag"] == "script"]
        if len(scripts) != 1:
            raise AssertionError(f"Expected one inline control script, found {len(scripts)}")
        ids = [node for node in self.elements if "id" in node["attrs"]]
        if len(ids) != len({node["attrs"]["id"] for node in ids}):
            raise AssertionError("Generated HTML contains duplicate element IDs")
        return {"script": scripts[0],
                "elements": {node["attrs"]["id"]: self.spec(node) for node in ids},
                "cards": [self.spec(node) for node in self.elements if "data-card" in node["attrs"]]}


NODE_DRIVER = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const copied = [];
let clipboardAttempts = 0;
let activeElement = null;
class Element {
  constructor(spec) {
    this.textContent = spec.textContent;
    this.value = spec.value;
    this.hidden = Object.hasOwn(spec.attrs, 'hidden');
    this.readOnly = Object.hasOwn(spec.attrs, 'readonly');
    this.dataset = {};
    for (const [key, value] of Object.entries(spec.attrs)) {
      if (key.startsWith('data-')) this.dataset[key.slice(5)] = value;
    }
    this.events = new Map();
    this.selectionStart = null;
    this.selectionEnd = null;
  }
  addEventListener(type, callback) {
    if (!this.events.has(type)) this.events.set(type, []);
    this.events.get(type).push(callback);
  }
  async dispatch(type) {
    for (const callback of this.events.get(type) || []) {
      await callback.call(this, {type, target: this, currentTarget: this});
    }
  }
  focus() { activeElement = this; }
  select() { this.selectionStart = 0; this.selectionEnd = this.value.length; }
}
const elements = Object.fromEntries(Object.entries(input.elements).map(([id, spec]) => [id, new Element(spec)]));
const cards = input.cards.map(spec => new Element(spec));
const document = {
  getElementById(id) { return elements[id] || null; },
  querySelectorAll(selector) {
    if (selector !== '[data-card]') throw new Error('Unsupported DOM selector: ' + selector);
    return cards;
  }
};
const navigator = {};
if (input.clipboard !== 'unavailable') {
  navigator.clipboard = {async writeText(value) {
    clipboardAttempts++;
    if (input.clipboard === 'denied') throw new Error('Clipboard permission denied');
    copied.push(value);
  }};
}
function state() {
  const prompt = elements['next-prompt'];
  return {
    hidden: cards.map(card => card.hidden),
    visibleStatuses: cards.filter(card => !card.hidden).map(card => card.dataset.status),
    count: elements['filter-result'].textContent,
    copyResult: elements['copy-result'].textContent,
    copied: [...copied], clipboardAttempts,
    promptValue: prompt.value, promptReadOnly: prompt.readOnly,
    promptFocused: activeElement === prompt,
    selectionStart: prompt.selectionStart, selectionEnd: prompt.selectionEnd
  };
}
(async () => {
  vm.runInNewContext(input.script, {document, navigator}, {timeout: 1000});
  const states = [state()];
  for (const action of input.actions) {
    const element = elements[action.id];
    if (!element) throw new Error('Missing rendered element: ' + action.id);
    if (Object.hasOwn(action, 'value')) element.value = action.value;
    await element.dispatch(action.event);
    states.push(state());
  }
  process.stdout.write(JSON.stringify(states));
})().catch(error => { process.stderr.write(error.stack); process.exitCode = 1; });
"""


@unittest.skipUnless(shutil.which("node"), "Node is required for generated-script DOM-event tests")
class ExperienceControls(unittest.TestCase):
    def model(self, empty=False):
        items = [] if empty else [
            {"id": "metric:shipped", "name": "Shipped units", "status": "released",
             "meaning": "Units on shipped orders", "provenance": ["orders.model"]},
            {"id": "metric:cost", "name": "Shipping cost", "status": "draft",
             "meaning": "Delivery spending", "provenance": ["rate-card.csv"]},
            {"id": "metric:returns", "name": "Return rate", "status": "held",
             "meaning": "Return ratio awaiting a required filter", "provenance": ["orders.model"]},
        ]
        for item in items:
            item.update(kind="metric", status_label=experience.LABELS[item["status"]],
                        calculation="SUM(units)", population="Selected source scope", sql="")
        return {"phase": {"label": "Review saved definitions", "code": "build", "status": "running"},
                "snapshot_at": "2026-11-08T12:00:00Z", "items": items,
                "sample": items[0] if items else None, "items_truncated": False,
                "counts": {"metrics": len(items), "queries": 0, "released": 0 if empty else 1,
                           "held": 0 if empty else 1},
                "scope": [{"name": "Shipping report"}], "selected_pages": [], "warnings": [],
                "next_step": "Review the saved definitions in chat.",
                "next_prompt": "Show the saved definition for Shipped units.\nKeep the run local & explain checks."}

    def run_controls(self, actions=(), clipboard="success", empty=False):
        # Only the public renderer supplies the script and controls under test.
        payload = GeneratedDOM(experience.render_html(self.model(empty))).payload()
        for required in ("filter", "status-filter", "filter-result", "next-prompt", "copy-prompt", "copy-result"):
            self.assertIn(required, payload["elements"])
        payload.update(actions=actions, clipboard=clipboard)
        result = subprocess.run([shutil.which("node"), "-e", NODE_DRIVER], input=json.dumps(payload),
                                text=True, capture_output=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_search_and_status_intersect_then_reset_without_losing_cards(self):
        actions = [
            {"id": "filter", "event": "input", "value": "sHiPpEd"},
            {"id": "status-filter", "event": "change", "value": "draft"},
            {"id": "filter", "event": "input", "value": "shipping"},
            {"id": "status-filter", "event": "change", "value": ""},
            {"id": "filter", "event": "input", "value": "ORDERS.MODEL"},
            {"id": "status-filter", "event": "change", "value": "held"},
            {"id": "filter", "event": "input", "value": ""},
            {"id": "status-filter", "event": "change", "value": ""},
        ]
        states = self.run_controls(actions)
        expected = [
            [False, False, False], [False, True, True], [True, True, True],
            [True, False, True], [True, False, True], [False, True, False],
            [True, True, False], [True, True, False], [False, False, False],
        ]
        for index, hidden in enumerate(expected):
            with self.subTest(event=index):
                self.assertEqual(states[index]["hidden"], hidden)
                self.assertEqual(states[index]["count"], f"{hidden.count(False)} definitions shown")
        self.assertEqual(states[6]["visibleStatuses"], ["held"])

    def test_empty_collection_and_unmatched_search_report_zero_results(self):
        actions = [{"id": "filter", "event": "input", "value": "no matching definition"},
                   {"id": "status-filter", "event": "change", "value": "released"}]
        for empty in (False, True):
            states = self.run_controls(actions, empty=empty)
            for state in states[1:]:
                with self.subTest(empty=empty, state=state):
                    self.assertTrue(all(state["hidden"]))
                    self.assertEqual(state["visibleStatuses"], [])
                    self.assertEqual(state["count"], "0 definitions shown")
            if empty:
                self.assertEqual(states[0]["count"], "0 definitions shown")

    def test_clipboard_success_copies_actual_rendered_prompt_once(self):
        before, after = self.run_controls([{"id": "copy-prompt", "event": "click"}])
        self.assertEqual(after["copied"], [self.model()["next_prompt"]])
        self.assertEqual(after["clipboardAttempts"], 1)
        self.assertEqual(after["copyResult"], "Prompt copied. Paste it in chat.")
        self.assertFalse(after["promptFocused"])
        self.assertIsNone(after["selectionStart"])
        self.assertTrue(after["promptReadOnly"])
        self.assertEqual(after["promptValue"], before["promptValue"])
        self.assertEqual(after["hidden"], before["hidden"])

    def test_clipboard_denied_or_missing_selects_the_whole_prompt_for_manual_copy(self):
        for mode, attempts in (("denied", 1), ("unavailable", 0)):
            with self.subTest(clipboard=mode):
                before, after = self.run_controls([{"id": "copy-prompt", "event": "click"}], clipboard=mode)
                self.assertEqual(after["copied"], [])
                self.assertEqual(after["clipboardAttempts"], attempts)
                self.assertEqual(after["copyResult"], "Select and copy the prompt, then paste it in chat.")
                self.assertTrue(after["promptFocused"])
                self.assertEqual(after["selectionStart"], 0)
                self.assertEqual(after["selectionEnd"], len(before["promptValue"]))
                self.assertTrue(after["promptReadOnly"])
                self.assertEqual(after["promptValue"], before["promptValue"])
                self.assertEqual(after["hidden"], before["hidden"])


if __name__ == "__main__":
    unittest.main()
