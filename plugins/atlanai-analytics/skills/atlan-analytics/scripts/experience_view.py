"""Host-visible Analytics presentation. A bounded, read-only loopback preview."""
import argparse
import base64
import hashlib
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path

from context_state import ordinary, digest
import experience

MAX_PAGE = 2_000_000


def overview(run, supplied=None):
    """Keep authored source understanding separate from verification evidence."""
    reader = experience._Reader(run.root)
    sources = reader.read('run.json', {}).get('sources', [])
    signature = digest(sources)
    if supplied is not None:
        if (not isinstance(supplied, dict) or set(supplied) != {'summary', 'opportunities'}
                or not isinstance(supplied['summary'], str) or not 1 <= len(supplied['summary']) <= 1200
                or not isinstance(supplied['opportunities'], list) or len(supplied['opportunities']) > 3
                or any(not isinstance(v, str) or not 1 <= len(v) <= 120 for v in supplied['opportunities'])):
            raise ValueError('Supply a short source summary and at most three possible analyses.')
        run.write(run.root / 'presentation.json', {**supplied, 'source_signature': signature})
    value = reader.read('presentation.json', {}) if supplied is None else {**supplied, 'source_signature': signature}
    return value if value.get('source_signature') == signature else {}


def pane(model, understanding):
    """Bounded text-only payload for Claude's native mod; no paths or executable actions."""
    sample = model.get('sample') or {}
    clean = lambda v, n=500: experience._text(v, n)
    return {'phase': clean(model['phase']['label'], 120), 'status': clean(model['phase']['status'], 80),
            'summary': clean(understanding.get('summary'), 1200),
            'sources': [clean(s['name'], 120) for s in model['scope'][:8]],
            'opportunities': [clean(s, 120) for s in understanding.get('opportunities', [])[:3]],
            'domain': clean(model['interaction']['intent'].get('domain'), 160),
            'purpose': clean(model['interaction']['intent'].get('purpose'), 240),
            'counts': ', '.join(f'{model["counts"][key]} {label}' for key, label in
                               [('metrics', 'metrics'), ('queries', 'queries'), ('released', 'saved'), ('held', 'held')]),
            'metric': clean(sample.get('name'), 120), 'meaning': clean(sample.get('meaning')),
            'calculation': clean(sample.get('calculation'), 2000), 'population': clean(sample.get('population')),
            'source': clean('; '.join(sample.get('provenance', []))), 'check': clean(sample.get('status_label'), 160),
            'warnings': [clean(w, 240) for w in model['warnings'][:5]],
            'next': clean(model['next_step']), 'snapshot_at': model['snapshot_at']}


STYLE = """
:root{color-scheme:light dark}*{box-sizing:border-box}body{margin:0;background:Canvas;color:CanvasText;font:15px/1.55 system-ui}
main{max-width:1060px;padding:28px;margin:auto}h1{font-size:26px;line-height:1.2;margin:8px 0}h2{font-size:18px;margin:0 0 10px}h3{font-size:15px}
.muted,small{opacity:.7}header{margin-bottom:24px}.rail{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}.rail span,.tag{border:1px solid GrayText;border-radius:20px;padding:4px 12px;font-size:13px}
[aria-current=step]{background:CanvasText;color:Canvas}.grid{display:grid;grid-template-columns:1.6fr 1fr;gap:18px}.card{border:1px solid color-mix(in srgb,CanvasText 18%,Canvas);border-radius:12px;padding:20px;margin:0 0 18px}
.summary{font-size:17px;max-width:76ch}.sources{display:flex;gap:8px;flex-wrap:wrap}.source{background:color-mix(in srgb,CanvasText 5%,Canvas);border-radius:7px;padding:8px 12px;font-size:13px}
.counts{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}.count strong{display:block;font-size:24px}.count span{font-size:12px;opacity:.7}
.metric h2{font-size:25px}.status{font-size:13px;border-inline-start:3px solid Highlight;padding-left:10px}.row{border-bottom:1px solid color-mix(in srgb,CanvasText 12%,Canvas);padding:12px 0}.row strong{display:block}
button,input,select{font:inherit;padding:8px 10px;max-width:100%}button,summary{cursor:pointer}button{border:1px solid GrayText;border-radius:7px;background:Canvas;color:CanvasText}button:hover{background:color-mix(in srgb,CanvasText 8%,Canvas)}
summary{padding:9px 0}pre{font:13px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;border-inline-start:2px solid GrayText;padding:12px}p{margin:8px 0 14px}.controls{display:flex;gap:8px;flex-wrap:wrap}
:focus-visible{outline:2px solid Highlight;outline-offset:3px}[hidden]{display:none}.warning{border-inline-start:3px solid Highlight;padding:10px}#connection{font-size:12px}.catalog{margin-top:12px}#copy-fallback{width:100%;font:inherit}
@media(max-width:650px){main{padding:16px}.grid{display:block}.counts{grid-template-columns:repeat(2,1fr)}}
"""

SCRIPT = r"""
let revision='',refreshing=false;
function filter(){const query=document.getElementById('filter').value.toLowerCase();const state=document.getElementById('status-filter').value;let count=0;
document.querySelectorAll('[data-card]').forEach(card=>{card.hidden=!(card.textContent.toLowerCase().includes(query)&&(!state||state===card.dataset.status));if(!card.hidden)count++;});document.getElementById('filter-result').textContent=count+' definitions shown';}
document.addEventListener('input',e=>{if(e.target.id==='filter')filter();});
document.addEventListener('change',e=>{if(e.target.id==='status-filter')filter();});
const prompts={details:'Show the calculation, SQL, source and checks for the selected metric in this analytics run.',pause:'Pause this analytics run at its current checkpoint.',resume:'Resume this analytics run from its saved checkpoint.',local:'Keep this analytics run local. Do not publish its definitions.'};
document.addEventListener('click',async e=>{const button=e.target.closest('[data-prompt]');if(!button)return;const prompt=prompts[button.dataset.prompt];if(!prompt)return;
const result=document.getElementById('copy-result');try{await navigator.clipboard.writeText(prompt);result.textContent='Copied. Paste in chat to submit your choice.';}catch{const field=document.getElementById('copy-fallback');field.hidden=false;field.value=prompt;field.focus();field.select();result.textContent='Copy this prompt and submit it in chat.';}});
async function refresh(){if(refreshing||document.hidden)return;refreshing=true;try{
const response=await fetch('/snapshot',{cache:'no-store'});if(!response.ok)throw Error('unavailable');const data=await response.json();
if(data.revision!==revision&&document.activeElement?.tagName!=='TEXTAREA'){
const ids=[...document.querySelectorAll('details[open]')].map(e=>e.id);const query=document.getElementById('filter').value;const state=document.getElementById('status-filter').value;const active=document.activeElement?.id;
const incoming=new DOMParser().parseFromString(data.html,'text/html').querySelector('main');if(!incoming)throw Error('invalid');document.querySelector('main').replaceWith(incoming);
ids.forEach(id=>{const el=document.getElementById(id);if(el)el.open=true;});document.getElementById('filter').value=query;document.getElementById('status-filter').value=state;filter();if(active)document.getElementById(active)?.focus();revision=data.revision;}
document.getElementById('connection').textContent='Following saved progress · '+new Date().toLocaleTimeString();
}catch{document.getElementById('connection').textContent='Updates unavailable. Showing the last received evidence. Resume the preview in chat.';}finally{refreshing=false;}}
if(location.protocol==='http:'&&location.hostname==='127.0.0.1'){refresh();setInterval(refresh,2500);}
"""


def render(model, understanding=None):
    understanding = understanding or {}
    esc = lambda v: html.escape(str(v), quote=True)
    counts, sample = model['counts'], model.get('sample')
    phase = model['phase']['code']
    step = 'Discover' if phase in ('inspect', 'scope') else 'Use' if phase == 'ready' else 'Prepare'
    rail = ''.join('<span'+(' aria-current="step"' if s == step else '')+'>'+s+'</span>' for s in ('Discover','Prepare','Use'))
    summary = understanding.get('summary') or 'These are the sources found in your selected scope. Choose what matters in the conversation.'
    sources = ''.join('<span class="source">'+esc(s['name'])+'</span>' for s in model['scope'][:8])
    opportunities = ''.join('<li>'+esc(v)+'</li>' for v in understanding.get('opportunities', []))
    intent = model['interaction']['intent']
    goal = '<p><strong>Business focus</strong><br>'+esc(intent.get('domain') or 'Choose in chat')+'</p><p><strong>Your goal</strong><br>'+esc(intent.get('purpose') or 'Choose after the business focus')+'</p>'
    count_html = ''.join('<div class="count"><strong>'+str(counts[key])+'</strong><span>'+label+'</span></div>' for key,label in [('metrics','Metric definitions'),('queries','Saved queries'),('released','Saved in Atlan'),('held','Need repair')])
    if sample:
        metric = '<p class="muted">Your first metric</p><h2>'+esc(sample['name'])+'</h2><p>'+esc(sample['meaning'])+'</p><p class="status">'+esc(sample['status_label'])+'</p>'
        metric += '<div class="row"><strong>Calculation</strong><pre>'+esc(sample['calculation'])+'</pre></div><div class="row"><strong>Who and what it includes</strong>'+esc(sample.get('population','Review source scope'))+'</div>'
        metric += '<details id="metric-evidence"><summary>Inspect SQL, source and checks</summary><pre>'+esc(sample.get('sql') or sample['calculation'])+'</pre><p>'+esc('; '.join(sample['provenance']))+'</p>'
        metric += ''.join('<p>'+esc(n)+'</p>' for n in sample.get('detail_notes',[]))+'<p class="muted">Saved checks do not prove fresh execution or dashboard parity.</p></details>'
    else:
        metric = '<h2>Your first metric will appear here</h2><p>The source overview is ready. We will retain the selected metric here as its definition and checks become available.</p>'
    catalog = ''
    for item in model['items']:
        ident = hashlib.sha256(item['id'].encode()).hexdigest()[:16]
        catalog += '<details class="row" id="item-'+ident+'" data-card data-status="'+esc(item['status'])+'"><summary>'+esc(item['name'])+' · '+esc(item['status_label'])+'</summary><p>'+esc(item['meaning'])+'</p><pre>'+esc(item.get('sql') or item['calculation'])+'</pre><p>'+esc(item.get('population',''))+'</p><p>'+esc('; '.join(item['provenance']))+'</p></details>'
    warnings = ''.join('<p class="warning">'+esc(w)+'</p>' for w in model['warnings'])
    modes = model['interaction']['mode']
    controls = [('details','Copy details request'), ('resume','Copy resume request') if modes!='active' else ('pause','Copy pause request'), ('local','Copy keep-local request')]
    buttons = ''.join('<button type="button" data-prompt="'+key+'">'+label+'</button>' for key,label in controls)
    options = ''.join('<option value="'+esc(k)+'">'+esc(v)+'</option>' for k,v in experience.LABELS.items())
    script_hash=base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'sha256-'+script_hash+'\'; connect-src \'self\'; base-uri \'none\'; form-action \'none\'"><title>Analytics · Your context</title><style>'+STYLE+'</style></head><body><main><header><small>ANALYTICS</small><h1>'+esc(model['phase']['label'])+'</h1><p id="connection">Saved evidence · '+esc(model['snapshot_at'])+'</p><nav class="rail" aria-label="Setup progress">'+rail+'</nav></header>'+warnings+'<section class="card"><h2>What we found</h2><p class="summary">'+esc(summary)+'</p><div class="sources">'+sources+'</div>'+('<h3>What this could help you explore</h3><ul>'+opportunities+'</ul>' if opportunities else '')+'</section><div class="grid"><section class="card metric">'+metric+'</section><aside><section class="card"><h2>What matters to you</h2>'+goal+'</section><section class="card"><h2>Up next</h2><p>'+esc(model['next_step'])+'</p><p class="muted">Decisions stay in your conversation.</p></section></aside></div><div class="counts">'+count_html+'</div><details id="catalog" class="card"><summary>Browse definitions and their evidence</summary><label for="filter">Find a definition</label><input id="filter" type="search" placeholder="Metric, query or source"><label for="status-filter"> Status </label><select id="status-filter"><option value="">All statuses</option>'+options+'</select><p id="filter-result">'+str(len(model['items']))+' definitions shown</p><div class="catalog">'+catalog+'</div>'+('<p>Showing the first 100 definitions. Ask in chat for more.</p>' if model['items_truncated'] else '')+'</details><section class="card"><h2>You are in control</h2><div class="controls">'+buttons+'</div><p id="copy-result" aria-live="polite">Copy a request, then submit it in chat. These buttons do not execute or publish.</p><textarea id="copy-fallback" hidden readonly rows="3" aria-label="Request to copy"></textarea></section></main><script>'+SCRIPT+'</script></body></html>'


def prepare(run, model, supplied=None):
    understanding = overview(run, supplied)
    page = render(model, understanding)
    run.write_text(run.root / 'experience.html', page)
    return {'status':'prepared', 'snapshot':str(run.root / 'experience.html'),
            'pane':pane(model, understanding), 'chat':experience.render_chat(model),
            'displayed':False, 'remote_writes':False,
            'next':'Call the available native pane or browser tool. A prepared file is not a visible UI.'}


class Preview(HTTPServer):
    allow_reuse_address = False


def make_server(root, port=0, engine_dir=None, host=None, server=None):
    root = ordinary(root)
    if not (root / 'run.json').is_file():
        raise ValueError('Select an existing analytics run.')
    experience.host_scope(host, server)
    class ReadOnly:
        def __init__(self): self.root=root
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            origin='http://127.0.0.1:'+str(self.server.server_port)
            if (self.headers.get('Host') != origin[7:] or self.headers.get('Origin',origin)!=origin
                    or self.headers.get('Sec-Fetch-Site')=='cross-site'):
                self.send_error(403);return
            if self.path not in ('/', '/snapshot'):
                self.send_error(404);return
            try:
                model=experience.snapshot(root, engine_dir, host=host, server=server)
                understanding=overview(ReadOnly())
                page=render(model, understanding)
                stable={**model, 'snapshot_at':''}
                data=(json.dumps({'revision':digest([stable,understanding]), 'html':page}) if self.path=='/snapshot' else page).encode()
                if len(data)>MAX_PAGE: raise ValueError('Preview too large')
            except (ValueError,OSError,KeyError,TypeError):
                self.send_error(503,'Saved view unavailable');return
            self.send_response(200)
            self.send_header('Content-Type','application/json' if self.path=='/snapshot' else 'text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(data)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','no-referrer')
            self.end_headers();self.wfile.write(data)
    return Preview(('127.0.0.1',port),Handler)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',required=True,type=Path)
    parser.add_argument('--port',type=int,default=0)
    parser.add_argument('--engine-dir',type=Path)
    parser.add_argument('--host',choices=('claude_code','codex'))
    parser.add_argument('--server')
    args=parser.parse_args()
    with make_server(args.run_dir,args.port,args.engine_dir,args.host,args.server) as preview:
        print(json.dumps({'url':'http://127.0.0.1:'+str(preview.server_port),'pid':os.getpid(),'read_only':True}),flush=True)
        try: preview.serve_forever()
        except KeyboardInterrupt: pass


if __name__=='__main__': main()
