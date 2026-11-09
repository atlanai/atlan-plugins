"""Shared socket conformance for the maintained Databricks input, without network."""
import pytest
from metrics_agent import sockets as S
from metrics_agent.testing.conformance import InputPlugConformance
from metrics_agent_databricks.plug import DatabricksDirect

class FakeRunner:
    kind='databricks'
    dialect='databricks'
    def __enter__(self):self.connect();return self
    def __exit__(self,*args):self.close()
    def connect(self):pass
    def close(self):pass
    def explain(self,sql):return 1
    def columns_of(self,table):return ['ID','AMOUNT']
    def columns_of_many(self,tables):return {t:['ID','AMOUNT'] for t in tables}
    def fetch(self,sql,limit):
        if 'system.query.history' in sql:
            if 'SELECT COUNT(*) FROM' in sql:return ['masked'],[(0,)]
            return ['sql','exec','users','id'],[('SELECT COUNT(*) FROM DEMO.MAIN.ORDERS',12,3,'statement_1')]
        return ['one'],[(1,)]
    def scan(self,sql,batch):yield [('MAIN','ORDERS','ID','INT',None)]

@pytest.fixture(autouse=True)
def fake_connection(monkeypatch):
    monkeypatch.setattr(DatabricksDirect,'sql_runner',lambda self:FakeRunner())

class TestDatabricks(InputPlugConformance):
    plug=DatabricksDirect
    settings={'workspace_id':'123'}
    env={'DATABRICKS_SERVER_HOSTNAME':'example.cloud.databricks.com','DATABRICKS_HTTP_PATH':'/sql/1.0/warehouses/example','DATABRICKS_TOKEN':'synthetic-token'}
    tables=['DEMO.MAIN.ORDERS']
    window=S.Window('2026-01-01','2026-02-01')
