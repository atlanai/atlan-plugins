"""The Tableau reader on a workbook whose published data source could not be downloaded: its own
copy of the data source's model, located by a lineage file beside it, resolves the tiles to
warehouse tables with their relationships; a .tds beside it still wins."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENGINE = ROOT / 'plugins/atlanai-analytics/skills/metrics-generation/modules/metrics/engine/src'
# The reader imports the engine's compiler, which needs the runtime's packages (PyYAML).
HAVE_ENGINE = importlib.util.find_spec('yaml') is not None
if HAVE_ENGINE:
    sys.path.insert(0, str(ENGINE))
    from metrics_agent.plugs.readers import tableau  # noqa: E402

ORDERS = 'ORDERS (SALES_DB.ORDERS)_' + 'A' * 32
CUSTOMERS = 'CUSTOMERS (SALES_DB.CUSTOMERS)_' + 'B' * 32


def record(cls: str, remote: str, family: str, oid: str, kind: str = 'integer') -> str:
    return (f"<metadata-record class='{cls}'><remote-name>{remote}</remote-name>"
            f"<local-name>[{remote}]</local-name><parent-name>[sqlproxy]</parent-name>"
            f"<family>{family}</family><local-type>{kind}</local-type>"
            f"<object-id>[{oid}]</object-id></metadata-record>")


# The shape Tableau Cloud writes for a workbook on a published data source: a sqlproxy
# connection, every relation `[sqlproxy]`, each field's table on its metadata record, and a
# column two tables share suffixed with its table.
WORKBOOK = f"""<?xml version='1.0' encoding='utf-8' ?>
<workbook version='18.1'>
  <datasources>
    <datasource caption='Sales Model' inline='true' name='sqlproxy.0abc' version='18.1'>
      <repository-location id='SalesModel' path='/t/example/datasources' revision='1.0' />
      <connection class='sqlproxy' dbname='SalesModel' directory='dataserver' port='80'>
        <relation type='collection'>
          <relation name='sqlproxy' table='[sqlproxy]' type='table' />
          <relation name='sqlproxy' table='[sqlproxy]' type='table' />
        </relation>
        <metadata-records>
          {record('measure', 'AMOUNT', 'ORDERS', ORDERS, 'real')}
          {record('measure', 'CUSTOMER_ID (ORDERS)', 'ORDERS', ORDERS)}
          {record('column', 'ORDER_DATE', 'ORDERS', ORDERS, 'date')}
          {record('measure', 'CUSTOMER_ID', 'CUSTOMERS', CUSTOMERS)}
          {record('column', 'REGION', 'CUSTOMERS', CUSTOMERS, 'string')}
        </metadata-records>
      </connection>
      <column caption='Amount' datatype='real' name='[AMOUNT]' role='measure' type='quantitative' />
      <column caption='Region' datatype='string' name='[REGION]' role='dimension' type='nominal' />
      <column caption='Customer Id (Orders)' datatype='integer' name='[CUSTOMER_ID (ORDERS)]' role='measure' type='quantitative' />
      <column caption='Customer Id' datatype='integer' name='[CUSTOMER_ID]' role='measure' type='quantitative' />
      <column caption='Revenue' datatype='real' name='[Calculation_1]' role='measure' type='quantitative'>
        <calculation class='tableau' formula='SUM([AMOUNT])' />
      </column>
      <object-graph>
        <objects>
          <object caption='CUSTOMERS' id='{CUSTOMERS}'>
            <properties context=''><relation name='sqlproxy' table='[sqlproxy]' type='table' /></properties>
          </object>
          <object caption='ORDERS' id='{ORDERS}'>
            <properties context=''><relation name='sqlproxy' table='[sqlproxy]' type='table' /></properties>
          </object>
        </objects>
        <relationships>
          <relationship>
            <expression op='='>
              <expression op='[CUSTOMER_ID (ORDERS)]' />
              <expression op='[CUSTOMER_ID]' />
            </expression>
            <first-end-point object-id='{ORDERS}' />
            <second-end-point object-id='{CUSTOMERS}' />
          </relationship>
        </relationships>
      </object-graph>
    </datasource>
  </datasources>
  <worksheets>
    <worksheet name='Revenue by region'>
      <table>
        <view>
          <datasources><datasource caption='Sales Model' name='sqlproxy.0abc' /></datasources>
          <datasource-dependencies datasource='sqlproxy.0abc'>
            <column caption='Revenue' datatype='real' name='[Calculation_1]' role='measure' type='quantitative'>
              <calculation class='tableau' formula='SUM([AMOUNT])' />
            </column>
            <column datatype='string' name='[REGION]' role='dimension' type='nominal' />
            <column-instance column='[Calculation_1]' derivation='User' name='[usr:Calculation_1:qk]' pivot='key' type='quantitative' />
            <column-instance column='[REGION]' derivation='None' name='[none:REGION:nk]' pivot='key' type='nominal' />
          </datasource-dependencies>
        </view>
        <rows>[sqlproxy.0abc].[none:REGION:nk]</rows>
        <cols>[sqlproxy.0abc].[usr:Calculation_1:qk]</cols>
      </table>
    </worksheet>
  </worksheets>
</workbook>
"""

LINEAGE = {"datasource": "Sales Model", "connection": "snowflake", "database": "SALES_DB",
           "schema": "GOLD", "tables": ["ORDERS", "CUSTOMERS"],
           "source": "the site's lineage page, upstream tables"}

# The data source's own file: a different schema, so a test can tell which one answered.
TDS = """<?xml version='1.0' encoding='utf-8' ?>
<datasource formatted-name='Sales Model' inline='true' version='18.1'>
  <repository-location id='SalesModel' path='/t/example/datasources' revision='1.0' />
  <connection class='snowflake' dbname='SALES_DB' schema='FROM_TDS'>
    <relation name='ORDERS' table='[FROM_TDS].[ORDERS]' type='table' />
  </connection>
  <column caption='Amount' datatype='real' name='[AMOUNT]' role='measure' type='quantitative' />
</datasource>
"""


def setUpModule():
    if not HAVE_ENGINE:
        raise unittest.SkipTest('the metrics engine needs PyYAML; run with the metrics runtime')


class PublishedLineageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.workbook = self.dir / 'sales.twb'
        self.workbook.write_text(WORKBOOK, encoding='utf-8')

    def tearDown(self):
        self.tmp.cleanup()

    def lineage(self, **change):
        doc = {**LINEAGE, **change}
        (self.dir / 'Sales Model.lineage.json').write_text(json.dumps(doc), encoding='utf-8')

    def parse(self):
        return tableau.parse(self.workbook, dialect='snowflake')

    def test_without_a_lineage_file_the_reference_stays_unresolved(self):
        facts = self.parse()
        self.assertEqual(facts['_unresolved_published'], ['Sales Model'])

    def test_lineage_file_rebuilds_the_tables_from_the_workbooks_copy(self):
        self.lineage()
        facts = self.parse()
        self.assertEqual(facts['_unresolved_published'], [])
        self.assertEqual(facts['_resolved_published'], ['Sales Model'])
        names = {t['name'] for t in facts['tables']}
        self.assertEqual(names, {'sales_db.gold.orders', 'sales_db.gold.customers'})
        cols = {t['name']: {c['source_column'] for c in t['columns']} for t in facts['tables']}
        # The table suffix Tableau adds to a shared column is not part of the warehouse name.
        self.assertIn('customer_id', cols['sales_db.gold.orders'])
        self.assertIn('region', cols['sales_db.gold.customers'])
        self.assertNotIn('customer_id (orders)', set().union(*cols.values()))

    def test_relationship_keeps_its_columns(self):
        self.lineage()
        [rel] = self.parse()['relationships']
        self.assertEqual({(rel['from_table'], rel['from_column']), (rel['to_table'], rel['to_column'])},
                         {('sales_db.gold.orders', 'CUSTOMER_ID'),
                          ('sales_db.gold.customers', 'CUSTOMER_ID')})

    def test_calculations_live_on_the_table_the_relationships_meet_at(self):
        # The copy lists CUSTOMERS first (name order); ORDERS is in the relationship and holds
        # the measure fields, so it is the primary table and the home of SUM([AMOUNT]).
        self.lineage()
        homes = {m['name']: m['home_table'] for m in self.parse()['measures']}
        self.assertEqual(homes['Revenue'], 'sales_db.gold.orders')

    def test_the_rebuild_is_said_in_the_readers_notes(self):
        self.lineage()
        notes = ' '.join(self.parse().get('_reader_notes', []))
        self.assertIn("rebuilt from the workbook's own copy", notes)
        self.assertIn('SALES_DB.GOLD', notes)

    def test_a_tds_beside_the_workbook_wins(self):
        self.lineage()
        (self.dir / 'Sales Model.tds').write_text(TDS, encoding='utf-8')
        facts = self.parse()
        self.assertIn('sales_db.from_tds.orders', {t['name'] for t in facts['tables']})
        self.assertNotIn("rebuilt from the workbook's own copy",
                         ' '.join(facts.get('_reader_notes', [])))

    def test_a_table_the_lineage_does_not_list_is_refused(self):
        self.lineage(tables=['ORDERS'])
        with self.assertRaisesRegex(tableau.TableauError, "CUSTOMERS"):
            self.parse()

    def test_a_name_that_is_not_plain_is_refused(self):
        for change in ({'schema': 'GOLD; DROP'}, {'database': '"SALES"'},
                       {'tables': ['ORDERS]']}, {'connection': 'Snow Flake'}):
            with self.subTest(change=change):
                self.lineage(**change)
                with self.assertRaises(tableau.TableauError):
                    self.parse()

    def test_a_schema_per_table_is_kept(self):
        self.lineage(tables=['ORDERS', 'REF.CUSTOMERS'])
        names = {t['name'] for t in self.parse()['tables']}
        self.assertEqual(names, {'sales_db.gold.orders', 'sales_db.ref.customers'})

    def test_a_lineage_file_for_another_datasource_is_left_unread(self):
        self.lineage(datasource='Inventory')
        facts = self.parse()
        self.assertEqual(facts['_unresolved_published'], ['Sales Model'])
        self.assertIn("'Inventory'", ' '.join(facts.get('_reader_notes', [])))


if __name__ == '__main__':
    unittest.main()
