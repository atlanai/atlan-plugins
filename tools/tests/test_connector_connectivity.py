"""Offline regressions for the four maintained connector authentication boundaries."""
import os
import unittest

from tools.tests import test_databricks_module as contracts
RUNTIME = contracts.RUNTIME


@unittest.skipUnless(RUNTIME, 'Set ANALYTICS_METRICS_RUNTIME for connector contracts.')
class ConnectivityTests(unittest.TestCase):
    def check(self, code):
        prefix = '''
for name in ('powerbi','tableau','snowflake'):
    sys.path.insert(0,str(ENGINE/f'packages/metrics-agent-{name}/src'))
import io,json,urllib.error
from types import SimpleNamespace
from metrics_agent_powerbi import plug as pbi,pbi_definition_extract as x
from metrics_agent_powerbi.credentials import TokenProvider
from metrics_agent_tableau import rest as tableau
from metrics_agent_snowflake.plug import connection_options as sf_options
'''
        contracts.DatabricksModuleTests.check(self, prefix + code)

    def test_databricks_dsn_isolation_and_browser_cache_provider(self):
        self.check('''
with mock.patch.dict(os.environ,{'METRICS_AGENT_WAREHOUSE_DSN':'account=example;user=synthetic'},clear=True),mock.patch.object(auth,'at_terminal',return_value=True):
    options=auth.connection_options({'server_hostname':'example.cloud.databricks.com','http_path':'/sql/1.0/warehouses/example','sign_in':'browser'})
    assert callable(options['credentials_provider']) and 'access_token' not in options
    import databricks.sdk.core as sdk
    with mock.patch.object(sdk,'Config',return_value=SimpleNamespace(authenticate=lambda:{})) as cfg:
        options['credentials_provider']()
        assert cfg.call_args.kwargs['auth_type']=='external-browser'
        assert cfg.call_args.kwargs['client_id']=='databricks-cli'
        assert cfg.call_args.kwargs['client_secret']==''
with mock.patch.dict(os.environ,{'DATABRICKS_DSN':'account=wrong'},clear=True):
    try:auth.connection_options({})
    except auth.AuthConfigurationError:pass
    else:raise AssertionError('Bad explicit DSN accepted')
with mock.patch.dict(os.environ,{},clear=True),mock.patch.object(auth,'at_terminal',return_value=True):
    settings={'server_hostname':'example.cloud.databricks.com','http_path':'/sql/1.0/warehouses/example','sign_in':'azure-browser'}
    a=auth.connection_options(settings);b=auth.connection_options(settings)
    assert a['experimental_oauth_persistence'] is b['experimental_oauth_persistence']
    c=auth.connection_options(dict(settings,server_hostname='other.cloud.databricks.com'))
    assert c['experimental_oauth_persistence'] is not a['experimental_oauth_persistence']
''')

    def test_snowflake_connector_flags_and_invalid_values(self):
        self.check('''
options=sf_options('account=example;user=synthetic;oauth_enable_refresh_tokens=false;client_session_keep_alive=true;login_timeout=30')
assert options['oauth_enable_refresh_tokens'] is False
assert options['client_session_keep_alive'] is True and options['login_timeout']==30
for bad in ['oauth_enable_refresh_tokens=synthetic-private','login_timeout=synthetic-private']:
    try:sf_options(bad)
    except ValueError as exc:assert 'synthetic-private' not in str(exc)
    else:raise AssertionError('Invalid option accepted')
''')

    def test_tableau_explicit_private_hosts_keep_metadata_and_tls_blocked(self):
        self.check('''
for host in ['10.0.0.5','tableau.internal','fd12::5']:
    url='https://'+('['+host+']' if ':' in host else host)
    try:tableau.check_server_url(url,['.online.tableau.com'])
    except tableau.TableauError:pass
    else:raise AssertionError('Private host automatically trusted')
    assert tableau.check_server_url(url,['.online.tableau.com'],[host])==url
for host in ['localhost','127.0.0.1','169.254.169.254','metadata.google.internal','100.100.100.200','fd00:0ec2:0000::254']:
    url='https://'+('['+host+']' if ':' in host else host)
    try:tableau.check_server_url(url,None,[host])
    except tableau.TableauError:pass
    else:raise AssertionError('Forbidden address accepted')
try:tableau.check_server_url('http://10.0.0.5',None,['10.0.0.5'])
except tableau.TableauError:pass
else:raise AssertionError('Plain HTTP accepted')
''')

    def test_tableau_jwt_uat_exchange_and_version(self):
        self.check('''
from metrics_agent_tableau.plug import TableauCloud
for variable,route in [('TABLEAU_JWT','jwt'),('TABLEAU_UAT','unified-access-token')]:
    with mock.patch.dict(os.environ,{variable:'synthetic-private'},clear=True):
        assert tableau.detect_route()==route
        session=tableau.Session('https://example.online.tableau.com','example','3.27',1)
        reply={'credentials':{'token':'synthetic-session','site':{'id':'00000000-0000-0000-0000-000000000001'}}}
        with mock.patch.object(session,'request',return_value=(200,{},json.dumps(reply).encode())) as request:
            session.sign_in(route)
            assert request.call_args.kwargs['body']['credentials']['jwt']=='synthetic-private'
            assert session.redact('synthetic-private synthetic-session')=='[redacted] [redacted]'
        if route=='unified-access-token':assert TableauCloud()._api_version()=='3.27'
        if route=='unified-access-token':
            try:TableauCloud({'api_version':'3.19'})._api_version()
            except tableau.TableauError:pass
            else:raise AssertionError('UAT selected with unsupported API version')
''')

    def test_tableau_transient_retries_are_bounded_and_auth_is_not_retried(self):
        self.check('''
def error(code,delay=None):
    return urllib.error.HTTPError('https://example.online.tableau.com/synthetic',code,'synthetic',{'Retry-After':delay} if delay else {},io.BytesIO(b'{}'))
session=tableau.Session('https://example.online.tableau.com','','3.19',1)
with mock.patch.object(session,'_request_once',side_effect=[tableau.TableauError('throttle',status=429),(200,{},b'{}')]) as request,mock.patch.object(tableau.time,'sleep') as sleep:
    assert session.request('GET','/synthetic')[0]==200
    assert request.call_count==2 and sleep.call_count==1
for code in [401,403,404]:
    with mock.patch.object(tableau,'_open',side_effect=error(code)) as request,mock.patch.object(tableau.time,'sleep') as sleep:
        try:session.request('GET','/synthetic')
        except tableau.TableauError:pass
        else:raise AssertionError('Denial swallowed')
        assert request.call_count==1 and not sleep.called
with mock.patch.object(tableau,'_open',side_effect=lambda *a,**k: (_ for _ in ()).throw(error(429,'1000'))) as request,mock.patch.object(tableau.time,'sleep') as sleep:
    try:session.request('GET','/synthetic')
    except tableau.TableauError as exc:assert 'cap' in str(exc)
    else:raise AssertionError('Wait cap bypassed')
    assert request.call_count==1 and not sleep.called
''')

    def test_powerbi_certificate_managed_identity_and_selection(self):
        self.check('''
import azure.identity as azure
env={'PBI_TENANT_ID':'synthetic-tenant','PBI_CLIENT_ID':'synthetic-client','PBI_CLIENT_CERTIFICATE_PATH':'/synthetic/client.pem'}
for mode,cls in [('certificate','CertificateCredential'),('managed-identity','ManagedIdentityCredential')]:
    with mock.patch.dict(os.environ,env,clear=True),mock.patch.object(azure,cls) as credential:
        credential.return_value.get_token.return_value=SimpleNamespace(token='synthetic-token',expires_on=9999999999)
        provider=TokenProvider(SimpleNamespace(auth=mode,scope=x.DEFAULT_SCOPE),x)
        assert provider()=='synthetic-token' and provider()=='synthetic-token'
        assert credential.call_count==1
        if mode=='certificate':assert credential.call_args.kwargs['certificate_path']=='/synthetic/client.pem'
        settings={'targets':['synthetic'],'sign_in':mode}
        assert pbi.sign_in(settings)==mode
        assert '--auth' in pbi.PowerBIFabric(settings).command(Path('/synthetic'))
with mock.patch.dict(os.environ,env,clear=True),mock.patch.object(azure,'CertificateCredential',side_effect=ValueError('synthetic-private')):
    try:TokenProvider(SimpleNamespace(auth='certificate',scope=x.DEFAULT_SCOPE),x)()
    except x.AuthError as exc:assert 'synthetic-private' not in str(exc)
    else:raise AssertionError('Vendor error escaped')
''')

    def test_powerbi_proactive_spn_and_interactive_refresh(self):
        self.check('''
args=SimpleNamespace(auth='spn',scope=x.DEFAULT_SCOPE)
def acquire(scope,**kw):kw['on_token']({'access_token':'synthetic-one','expires_in':3600});return 'synthetic-one'
with mock.patch.object(x,'token_client_credentials',side_effect=acquire) as token:
    provider=TokenProvider(args,x)
    assert provider()=='synthetic-one'
    provider.expires_at=0
    assert provider()=='synthetic-one' and token.call_count==2
args=SimpleNamespace(auth='device',scope=x.DEFAULT_SCOPE,tenant='synthetic',client_id='synthetic')
def initial(args,**kw):kw['on_token']({'access_token':'synthetic-one','refresh_token':'synthetic-refresh','expires_in':3600});return 'synthetic-one'
with mock.patch.object(x,'acquire_token',side_effect=initial) as initial_call,mock.patch.object(x,'_form_post',return_value=(200,{'access_token':'synthetic-two','expires_in':3600})) as refresh:
    provider=TokenProvider(args,x)
    assert provider()=='synthetic-one'
    provider.expires_at=0
    assert provider()=='synthetic-two'
    assert initial_call.call_count==1 and refresh.call_args.args[1]['grant_type']=='refresh_token'
''')

    def test_powerbi_expiry_retries_once_for_api_and_downloads(self):
        self.check('''
def expired():return urllib.error.HTTPError('https://api.fabric.microsoft.com/synthetic',401,'expired',{},io.BytesIO(b'{}'))
class Response(io.BytesIO):
    status=200;headers={}
for download in [False,True]:
    provider=mock.Mock(return_value='synthetic-token')
    client=x.FabricClient('synthetic-token',token_provider=provider)
    client.max_attempts=1
    import tempfile
    with tempfile.TemporaryDirectory() as directory,mock.patch.object(x.urllib.request,'urlopen',side_effect=[expired(),Response(b'{}')]) as request:
        if download:client.download('https://api.fabric.microsoft.com/synthetic',str(Path(directory)/'file'))
        else:client._request('GET','https://api.fabric.microsoft.com/synthetic')
        assert request.call_count==2 and mock.call(force=True) in provider.call_args_list
    with mock.patch.object(x.urllib.request,'urlopen',side_effect=lambda *a,**k: (_ for _ in ()).throw(expired())) as request:
        try:client._request('GET','https://api.fabric.microsoft.com/synthetic')
        except x.ApiError:pass
        else:raise AssertionError('Repeated 401 swallowed')
        assert request.call_count==2
''')

    def test_powerbi_device_flow_requests_renewal_and_supplied_tokens_do_not_loop(self):
        self.check('''
payloads=[(200,{'device_code':'synthetic-device','expires_in':300,'interval':1}),(200,{'access_token':'synthetic-access','refresh_token':'synthetic-refresh','expires_in':3600})]
remember=mock.Mock()
with mock.patch.object(x,'_form_post',side_effect=payloads) as post,mock.patch.object(x.time,'sleep'),mock.patch.object(x,'log'):
    token,ttl=x.token_device_code('synthetic-tenant','synthetic-client',x.DEFAULT_SCOPE,on_token=remember)
    assert token=='synthetic-access' and ttl==3600
    assert 'offline_access' in post.call_args_list[0].args[1]['scope']
    assert remember.call_args.args[0]['refresh_token']=='synthetic-refresh'
args=SimpleNamespace(auth='token',scope=x.DEFAULT_SCOPE,tenant='synthetic')
with mock.patch.object(x,'acquire_token',return_value='synthetic-token'),mock.patch.object(x,'token_claims',return_value={}):
    provider=TokenProvider(args,x)
    assert provider()=='synthetic-token'
    try:provider(force=True)
    except x.AuthError as exc:assert 'PBI_ACCESS_TOKEN' in str(exc)
    else:raise AssertionError('Externally supplied token silently renewed')
''')
