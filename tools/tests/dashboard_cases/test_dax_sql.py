import copy
import sqlite3
import unittest

from trident.dax_sql import translate, TranslationGap
import dashboard_fixture as fixture


def mapping():
    return {'semantic_table':'Accounts', 'relation':['warehouse_accounts'], 'evidence_ref':'synthetic:source-mapping',
        'rowset_equivalent':True, 'security_preserved':True,
        'columns':[{'semantic':name,'physical':physical,'datatype':kind} for name,physical,kind in
                   [('ID','id','integer'),('Active','active','boolean'),('Amount','amount','decimal'),
                    ('Tier','tier','string'),('Change','change','string'),('Source','source','string')]]}


class DaxSQL(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE warehouse_accounts(id INTEGER, active BOOLEAN, amount REAL, tier TEXT, change TEXT, source TEXT)')
        self.db.executemany('INSERT INTO warehouse_accounts VALUES(?,?,?,?,?,?)',[
            (1,True,10,'A','Upgrade','Direct'),(1,True,20,'A','Upgrade','Direct'),
            (2,False,30,'B','Downgrade','Direct'),(None,None,None,None,None,None)])

    def run_dax(self, dax, context=None, m=None):
        out=translate(dax,m or mapping(),context or {'filters':[]},'sqlite')
        return self.db.execute(out['sql'],out['parameters']).fetchone()[0]

    def test_distinctcount_includes_blank(self):
        self.assertEqual(self.run_dax("DISTINCTCOUNT('Accounts'[ID])"),3)

    def test_empty_countrows_and_distinctcount_are_blank(self):
        for measure in ["COUNTROWS('Accounts')","DISTINCTCOUNT('Accounts'[ID])"]:
            scope={'filters':[{'table':'Accounts','column':'Tier','values':['missing']}]}
            self.assertIsNone(self.run_dax(measure,scope))
            self.assertEqual(self.run_dax(measure+' + 0',scope),0)

    def test_aggregate_grain_and_nulls(self):
        self.assertEqual(self.run_dax("SUM('Accounts'[Amount])"),60)
        self.assertEqual(self.run_dax("AVERAGE('Accounts'[Amount])"),20)

    def test_calculate_replaces_same_column_filter(self):
        scope={'filters':[{'table':'Accounts','column':'Tier','values':['B']}]}
        dax='CALCULATE(COUNTROWS(\'Accounts\'), \'Accounts\'[Tier] = "A")'
        self.assertEqual(self.run_dax(dax,scope),2)

    def test_keepfilters_intersects(self):
        scope={'filters':[{'table':'Accounts','column':'Tier','values':['B']}]}
        dax='CALCULATE(COUNTROWS(\'Accounts\'), KEEPFILTERS(\'Accounts\'[Tier] = "A")) + 0'
        self.assertEqual(self.run_dax(dax,scope),0)

    def test_security_filter_is_not_overwritten(self):
        m=mapping();m['mandatory_filters']=[{'table':'Accounts','column':'Tier','values':['B']}]
        dax='CALCULATE(COUNTROWS(\'Accounts\'), \'Accounts\'[Tier] = "A") + 0'
        self.assertEqual(self.run_dax(dax,m=m),0)

    def test_loose_false_includes_blank_strict_false_does_not(self):
        self.assertEqual(self.run_dax("CALCULATE(COUNTROWS('Accounts'), 'Accounts'[Active] = FALSE())"),2)
        self.assertEqual(self.run_dax("CALCULATE(COUNTROWS('Accounts'), 'Accounts'[Active] == FALSE())"),1)

    def test_not_isblank(self):
        self.assertEqual(self.run_dax("CALCULATE(COUNTROWS('Accounts'), NOT ISBLANK('Accounts'[Amount]))"),3)

    def test_selectedvalue_blank_is_a_distinct_value(self):
        self.assertIsNone(self.run_dax("SELECTEDVALUE('Accounts'[Tier])"))
        self.assertEqual(self.run_dax("SELECTEDVALUE('Accounts'[Tier])",{'filters':[{'table':'Accounts','column':'Tier','values':['A']}]}),'A')

    def test_variables_and_net_change(self):
        dax='''VAR up = CALCULATE(DISTINCTCOUNT('Accounts'[ID]), CONTAINSSTRING('Accounts'[Change], "Upgrade"))
VAR down = CALCULATE(DISTINCTCOUNT('Accounts'[ID]), CONTAINSSTRING('Accounts'[Change], "Downgrade"))
RETURN (up + 0) - (down + 0)'''
        self.assertEqual(self.run_dax(dax),0)

    def test_source_literal_is_bound_not_interpolated(self):
        literal="x'); DROP TABLE warehouse_accounts; --"
        dax='CALCULATE(COUNTROWS(\'Accounts\'), \'Accounts\'[Tier] = "'+literal+'") + 0'
        result=translate(dax,mapping(),{'filters':[]},'sqlite')
        self.assertNotIn('DROP TABLE',result['sql'])
        self.assertIn(literal,result['parameters'].values())
        self.assertEqual(self.db.execute(result['sql'],result['parameters']).fetchone()[0],0)

    def test_variable_is_evaluated_before_calculate(self):
        dax = 'VAR total = COUNTROWS(\'Accounts\') RETURN CALCULATE(total, \'Accounts\'[Tier] = "A")'
        self.assertEqual(self.run_dax(dax), 4)

    def test_unsupported_types_and_precision_are_gaps(self):
        for dax in ["SUM('Accounts'[Tier])", "SELECTEDVALUE('Accounts'[Active])", '1.1234567890123456789']:
            with self.assertRaises(TranslationGap):
                translate(dax, mapping(), {'filters':[]}, 'postgres')

    def test_unsupported_time_and_relationship_syntax_refused(self):
        for dax in ["TOTALYTD(SUM('Accounts'[Amount]), 'Dates'[Date])", "SUMX('Accounts', 'Accounts'[Amount])", "CALCULATE(COUNTROWS('Accounts'), ALL('Accounts'))", 'SWITCH(TRUE(), 1, 2, 3)', '[Another Measure]', "CALCULATE(COUNTROWS('Accounts'), CONTAINSSTRING('Accounts'[Tier], \"A*\"))"]:
            with self.assertRaises(TranslationGap): translate(dax,mapping(),{'filters':[]},'sqlite')

    def test_mapping_and_security_required(self):
        for flag in ('rowset_equivalent','security_preserved'):
            m=mapping();m[flag]=False
            with self.assertRaises(TranslationGap): translate("COUNTROWS('Accounts')",m,{'filters':[]},'sqlite')
        with self.assertRaises(TranslationGap): translate("SUM('Accounts'[Missing])",mapping(),{'filters':[]},'sqlite')

    def test_dialects_and_identifier_escaping(self):
        for dialect in ('snowflake','postgres','sqlite'):
            m=mapping();m['relation']=['example_db','schema','a"b']
            out=translate("COUNTROWS('Accounts')",m,{'filters':[]},dialect)
            self.assertIn('"example_db"."schema"."a""b"',out['sql'])


class SQLWorkflow(unittest.TestCase):
    setUp = fixture.Workflow.setUp
    call = fixture.Workflow.call
    prepared = fixture.Workflow.prepared
    case = fixture.Workflow.case
    evaluate = fixture.Workflow.evaluate
    def test_sql_candidate_requires_bound_warehouse_eval(self):
        p=self.prepared()
        p=self.call('dashboard_translate',{'job_id':self.job,'expected_revision':p['revision'],'dialect':'sqlite','mappings':[mapping()]})
        self.assertEqual(p['summary']['sql_candidates'],1)
        self.assertEqual(p['summary']['warehouse_portable'],0)
        case=self.case(p)
        p=self.evaluate(p,case)
        self.assertEqual(p['evals'][0]['verdict'],'warehouse_query_not_bound')
        case=self.case(p);case['actual'].update(executor='warehouse',query_hash=p['metrics'][0]['translation']['query_hash'])
        p=self.evaluate(p,case)
        self.assertEqual(p['summary']['warehouse_portable'],1)
        full=self.call('dashboard_read',{'job_id':self.job,'include_definitions':True})
        d=full['definitions'][0]['definition']
        self.assertEqual(d['dialect'],'sqlite')
        self.assertEqual(d['extensions']['trident_dashboard']['original_dax'],fixture.capture()['measures'][0]['expression'])

    def test_missing_mapping_keeps_explicit_gap(self):
        p=self.prepared()
        p=self.call('dashboard_translate',{'job_id':self.job,'expected_revision':p['revision'],'dialect':'snowflake','mappings':[]})
        self.assertEqual(p['summary']['sql_candidates'],0)
        self.assertEqual(p['metrics'][0]['translation']['status'],'needs_authoring')
