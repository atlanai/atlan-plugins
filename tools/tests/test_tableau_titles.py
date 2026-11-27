"""A Tableau title reads as Tableau shows it: its tokens filled in, its paragraph breaks dropped."""
import importlib.util
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine'
DEPENDENCIES = all(importlib.util.find_spec(name) for name in ('sqlglot', 'yaml', 'jsonschema'))

if DEPENDENCIES:
    sys.path.insert(0, str(ENGINE / 'src'))
    from metrics_agent.plugs.readers import tableau

# Tableau Desktop's default title is the `<Sheet Name>` token, written escaped in the file.
# A paragraph break is a run of its own holding "Æ" and a newline. The worksheet's axis has a
# title of its own, and the dashboard places a sheet whose title is also a token.
WORKBOOK = """<workbook>
  <repository-location id='Sales_360' path='/t/site/workbooks' site='site' />
  <worksheets>
    <worksheet name='Sales by Rep'>
      <layout-options><title><formatted-text>
        <run bold='true'>&lt;Sheet Name&gt;</run>
      </formatted-text></title></layout-options>
      <table><style><style-rule element='axis'><encoding><axis><title><formatted-text>
        <run>Revenue axis</run>
      </formatted-text></title></axis></encoding></style-rule></style></table>
    </worksheet>
    <worksheet name='Untitled sheet'>
      <table><style><style-rule element='axis'><encoding><axis><title><formatted-text>
        <run>Only an axis title</run>
      </formatted-text></title></axis></encoding></style-rule></style></table>
    </worksheet>
    <worksheet name='Trends'>
      <layout-options><title><formatted-text>
        <run>Trends in &lt;Workbook Name&gt; for &lt;[ds].[none:REGION:nk]&gt;</run>
      </formatted-text></title></layout-options>
    </worksheet>
  </worksheets>
  <dashboards>
    <dashboard name='Business 360'>
      <layout-options><title><formatted-text>
        <run bold='true'>  &lt;Sheet Name&gt;</run>
        <run>Æ
</run>
        <run>    An overview of the business. </run>
      </formatted-text></title></layout-options>
      <zones><zone name='Sales by Rep'><layout-options><title><formatted-text>
        <run>A placed sheet's title</run>
      </formatted-text></title></layout-options></zone></zones>
      <devicelayouts><devicelayout name='Phone'><layout-options><title><formatted-text>
        <run bold='true'>  &lt;Sheet Name&gt;</run>
        <run>Æ
</run>
        <run>    An overview of the business. </run>
      </formatted-text></title></layout-options></devicelayout></devicelayouts>
    </dashboard>
  </dashboards>
</workbook>"""


@unittest.skipUnless(DEPENDENCIES, 'needs the metrics engine dependencies (sqlglot, PyYAML, jsonschema)')
class TableauTitleTest(unittest.TestCase):
    def setUp(self):
        self.root = ET.fromstring(WORKBOOK)
        self.sheets = {ws.get('name'): ws for ws in self.root.iter('worksheet')}

    def test_the_sheet_name_token_reads_as_the_sheet_name(self):
        self.assertEqual(tableau._title_of(self.sheets['Sales by Rep'], 'Sales by Rep'),
                         'Sales by Rep')

    def test_an_axis_title_is_not_the_sheet_title(self):
        self.assertEqual(tableau._title_of(self.sheets['Untitled sheet'], 'Untitled sheet'),
                         'Untitled sheet')

    def test_the_workbook_token_fills_in_and_a_field_token_stays(self):
        self.assertEqual(tableau._title_of(self.sheets['Trends'], 'Trends', 'Sales_360'),
                         'Trends in Sales_360 for <[ds].[none:REGION:nk]>')

    def test_a_dashboard_title_is_its_own_first_paragraph(self):
        placed = tableau._dashboards_of(self.root, 'Sales_360')['Sales by Rep']
        self.assertEqual([(d['name'], d['title']) for d in placed],
                         [('Business 360', 'Business 360')])

    def test_dashboard_prose_drops_breaks_and_repeats(self):
        self.assertEqual(tableau._dashboard_prose(self.root, 'Sales_360')['Business 360'],
                         ['Business 360', 'An overview of the business.', "A placed sheet's title"])

    def test_text_of_drops_the_paragraph_run(self):
        title = self.root.find('./dashboards/dashboard/layout-options/title')
        self.assertEqual(tableau._text_of(title), '<Sheet Name> An overview of the business.')


if __name__ == '__main__':
    unittest.main()
