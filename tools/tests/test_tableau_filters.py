"""The Tableau reader's date filters: a pinned month compares as a date, and a relative date
filter written by Tableau 2025.1 keeps its period."""
import importlib.util
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine/src'
# The reader imports the engine's compiler, which needs the runtime's packages (PyYAML).
HAVE_ENGINE = importlib.util.find_spec('yaml') is not None
if HAVE_ENGINE:
    sys.path.insert(0, str(ENGINE))
    from metrics_agent.plugs.readers import tableau  # noqa: E402


def setUpModule():
    if not HAVE_ENGINE:
        raise unittest.SkipTest('the metrics engine needs PyYAML; run with the metrics runtime')


def read(filters: str) -> list[dict]:
    ws = ET.fromstring(f'<worksheet><table><view>{filters}</view></table></worksheet>')
    return tableau._filters(ws, {}, date_cols=[])


class TruncatedDateMemberTest(unittest.TestCase):
    def member(self, pill: str, member: str):
        [f] = read(f"<filter class='categorical' column='[ds].[{pill}:EFFECTIVE_DATE:ok]'>"
                   f"<groupfilter function='member' level='[{pill}:EFFECTIVE_DATE:ok]' "
                   f"member='{member}' /></filter>")
        return f['op'], f['value']

    def test_month_year_member_is_the_first_day_of_its_month(self):
        self.assertEqual(self.member('my', '202611'), ('in', '2026-11-01'))

    def test_month_day_year_member_is_its_date(self):
        self.assertEqual(self.member('mdy', '20261105'), ('in', '2026-11-05'))

    def test_continuous_month_member_loses_its_date_marks(self):
        self.assertEqual(self.member('tmn', '#2026-11-01 00:00:00#'), ('in', '2026-11-01'))

    def test_a_member_that_is_not_a_date_is_kept_as_written(self):
        self.assertEqual(self.member('my', '202613'), ('in', '202613'))
        self.assertEqual(self.member('yr', '2026'), ('in', '2026'))


class RelativeDatePeriodTest(unittest.TestCase):
    def window(self, attr: str):
        [f] = read(f"<filter class='relative-date' column='[ds].[none:EFFECTIVE_DATE:qk]' "
                   f"filter-group='6' first-period='-2' last-period='0' include-future='true' "
                   f"include-null='false' {attr}='month' />")
        return f['value']

    def test_period_type_v2_reads_as_period_type(self):
        self.assertEqual(self.window('period-type-v2'), self.window('period-type'))
        self.assertEqual(self.window('period-type-v2'),
                         {'unknown': [], 'period': 'month', 'first': -2, 'last': 0,
                          'include_null': False, 'include_future': True})


if __name__ == '__main__':
    unittest.main()
