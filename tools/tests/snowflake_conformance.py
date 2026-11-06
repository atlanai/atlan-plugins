"""Focused offline socket conformance for the native Snowflake history route."""
from datetime import datetime, timezone
import pytest
from metrics_agent import sockets as S
from metrics_agent.testing.conformance import InputPlugConformance
from metrics_agent_snowflake.plug import SnowflakeDirect

class FakeRunner:
    kind='snowflake'
    dialect='snowflake'
    def __enter__(self): self.connect(); return self
    def __exit__(self,*args): self.close()
    def connect(self): pass
    def close(self): pass
    def explain(self,sql): return 1
    def columns_of(self,table): return ['ID','AMOUNT']
    def columns_of_many(self,tables): return {table:['ID','AMOUNT'] for table in tables}
    def fetch(self,sql,limit):
        if 'ACCOUNT_USAGE.QUERY_HISTORY' in sql:
            return ['query_text','exec_count','user_count','query_id','database_name','schema_name'], [('SELECT COUNT(*) FROM DEMO.MAIN.ORDERS',12,3,'query_1','DEMO','MAIN')]
        if 'CURRENT_DATABASE' in sql:return ['database'],[('DEMO',)]
        if 'GET_DDL' in sql:return ['ddl'],[('CREATE SEMANTIC VIEW DEMO.MAIN.SALES_VIEW TABLES (o AS DEMO.MAIN.ORDERS PRIMARY KEY (ID)) METRICS (o.orders AS COUNT(o.ID))',)]
        return ['one'],[(1,)]
    def scan(self,sql,batch):
        if 'information_schema.columns' in sql.lower():yield [('MAIN','ORDERS','ID','INTEGER',None)]
        else:yield self.fetch(sql,batch)[1]

@pytest.fixture(autouse=True)
def fake_connection(monkeypatch):
    monkeypatch.setattr(SnowflakeDirect,'sql_runner',lambda self:FakeRunner())

class TestNativeHistory(InputPlugConformance):
    plug=SnowflakeDirect
    settings={'history_source':'account_usage','catalog':'DEMO','semantic_views':['DEMO.MAIN.SALES_VIEW']}
    env={'METRICS_AGENT_WAREHOUSE_DSN':'account=example;user=reader'}
    tables=['DEMO.MAIN.ORDERS']
    window=S.Window('2026-01-01','2026-02-01')
