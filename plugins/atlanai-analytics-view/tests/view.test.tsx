import {describe,expect,test,mock} from 'claude-code/testing'
import {valid,validFeed,helperLabel,countLine} from '../hooks/register'
const TOOL='mcp__atlanai-analytics-view__show'
const FOLLOW='mcp__atlanai-analytics-view__follow'
const view={phase:'Checking definitions',status:'running',summary:'Synthetic shipping model and query history.',domain:'Shipping cost',purpose:'Prepare reusable metrics',counts:'1 metrics, 0 queries, 0 saved, 0 held',metric:'Shipping cost',meaning:'Cost for shipped shipments.',calculation:'SUM(cost)',population:'Shipped shipments only',source:'Synthetic model',check:'SQL untested',next:'Check the definition.',snapshot_at:'2026-11-09T00:00:00Z',sources:['shipping.bim'],opportunities:['Cost by region'],warnings:[]}
const pane={plugin:'atlanai-analytics-view',surface:'terminal',component:'Pane',requestId:'analytics-view',props:{title:'Analytics',isFocused:false,bodyColumns:60,placement:'dock',scroll:{offset:0,bodyRows:40},view:{}}} as const
describe('Analytics native pane',()=>{
 test('discovery shows source understanding without empty evidence or premature exploration',async($,on)=>{
  on('ui.open',()=>({value:undefined}))
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  await $.tool.call({tool:TOOL,view:{...view,phase_code:'scope',metric:'',domain:'',purpose:'',meaning:'',next:'Choose your business focus in chat.',
   stats:{metrics:0,queries:0,executed:0,review_required:0,released:0,held:0}}})
  const ui=await $.ui.mount({...pane,surface:'desktop'})
  expect((await ui.find({type:'Text',text:view.summary}))?.text).toBe(view.summary)
  expect((await ui.find({type:'Text',text:'Choose your business focus in chat.'}))?.text).toBe('Choose your business focus in chat.')
  expect(await ui.find({type:'Button',key:'evidence'})).toBeUndefined()
  expect(await ui.find({type:'Button',key:'ask'})).toBeUndefined()
  expect(await ui.find({type:'Text',text:'SQL checks'})).toBeUndefined()
  await ui.unmount()
 })
 test('desktop starts with a visual board and reveals long evidence only on request',async($,on)=>{
  on('ui.open',()=>({value:undefined}))
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  let filled=''
  on('prompt.fill',(_,e)=>{filled=e.text;return {isFilled:true}})
  await $.tool.call({tool:TOOL,view:{...view,phase_code:'preview',interaction_mode:'local',
   stats:{metrics:1,queries:2,executed:3,review_required:2,released:0,held:0}}})
  const ui=await $.ui.mount({...pane,surface:'desktop'})
  expect(await ui.find({type:'Svg'})).toBeUndefined()
  expect((await ui.find({type:'Text',text:'Shipping cost'}))?.text).toBe('Shipping cost')
  expect((await ui.find({type:'Text',text:'Saved locally'}))?.text).toBe('Saved locally')
  expect((await ui.find({type:'Text',text:'2 queries need source review'}))?.text).toBe('2 queries need source review')
  expect(await ui.find({type:'Text',text:view.summary})).toBeUndefined()
  expect(await ui.find({type:'Text',text:/Calculation:/})).toBeUndefined()
  await ui.press({key:'evidence'})
  expect((await ui.find({type:'Text',text:'Calculation: SUM(cost)'}))?.text).toBe('Calculation: SUM(cost)')
  await ui.press({key:'sources'})
  expect((await ui.find({type:'Text',text:view.summary}))?.text).toBe(view.summary)
  await ui.press({key:'ask'})
  expect(filled).toContain('source review')
  await ui.unmount()
 })
 test('desktop keeps supplied markup literal in native text',async($,on)=>{
  on('ui.open',()=>({value:undefined}))
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  const metric='<script>alert(1)</script>'
  await $.tool.call({tool:TOOL,view:{...view,metric}})
  const ui=await $.ui.mount({...pane,surface:'desktop'})
  expect((await ui.find({type:'Text',text:metric}))?.text).toBe(metric)
  expect(await ui.find({type:'Svg'})).toBeUndefined()
  expect(valid({...view,stats:{metrics:1}})).toBe(false)
  expect(valid({...view,phase_code:'publish-now'})).toBe(false)
  await ui.unmount()
 })
 test('validates bounded text without commands',()=>{
  expect(valid(view)).toBe(true);expect(valid({...view,command:'publish'})).toBe(false)
  expect(valid({...view,summary:'x'.repeat(2001)})).toBe(false)
 })
 test('opens, draws, updates and preserves dismissal',async($,on)=>{
  let opened=0
  on('ui.open',()=>{opened++;return {value:undefined}})
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  on('ui.close',()=>({value:undefined}))
  const shown=await $.tool.call({tool:TOOL,view})
  expect(JSON.parse(String(shown.result)).displayed).toBe(true)
  expect(opened).toBe(1)
  const ui=await $.ui.mount(pane)
  expect((await ui.find({type:'Text',text:/Synthetic shipping/}))?.text).toBe(view.summary)
  expect((await ui.find({type:'Text',text:/SQL untested/}))?.text).toBe('SQL untested')
  await ui.press({key:'hide'})
  await ui.unmount()
  await $.tool.call({tool:TOOL,view:{...view,check:'SQL checked'}})
  expect(opened).toBe(1)
 })
 test('reports unplaced panes honestly',async($,on)=>{
  on('ui.open',()=>({value:undefined}))
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:false,isShown:false,isFocused:false}]}))
  const result=await $.tool.call({tool:TOOL,view})
  expect(JSON.stringify(result)).toContain('text_fallback')
 })
 test('short terminal pane prioritizes the selected result and expands evidence on demand',async($,on)=>{
  on('ui.open',()=>({value:undefined}))
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  await $.tool.call({tool:TOOL,view})
  const ui=await $.ui.mount({...pane,props:{...pane.props,placement:'inline',bodyColumns:100,scroll:{offset:0,bodyRows:8}}})
  expect((await ui.find({type:'Text',text:'Shipping cost · SQL untested'}))?.text).toBe('Shipping cost · SQL untested')
  expect(await ui.find({type:'Text',text:view.summary})).toBeUndefined()
  await ui.press({key:'evidence'})
  expect((await ui.find({type:'Text',text:view.summary}))?.text).toBe(view.summary)
  await ui.press({key:'compact'})
  expect(await ui.find({type:'Text',text:view.summary})).toBeUndefined()
  await ui.unmount()
 })
 test('buttons reveal evidence and fill requests without executing them',async($,on)=>{
  const filled:string[]=[]
  on('ui.open',()=>({value:undefined}))
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  on('prompt.fill',(_,e)=>{filled.push(e.text);return {isFilled:true}})
  await $.tool.call({tool:TOOL,view})
  const ui=await $.ui.mount(pane)
  await ui.press({key:'evidence'})
  expect((await ui.find({type:'Text',text:/Saved checks are not fresh execution/}))?.text).toContain('Synthetic model')
  await ui.press({key:'details'});await ui.press({key:'pause'});await ui.press({key:'local'})
  expect(filled).toEqual(['Show the selected analytics metric, its SQL, source and checks.','Pause this analytics run at its current checkpoint.','Keep this analytics run local. Do not publish its definitions.'])
  await ui.unmount()
 })
 test('updates the same pane without reopening or stealing focus',async($,on)=>{
  let opened=0
  let filled=''
  on('ui.open',()=>{opened++;return {value:undefined}})
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  on('prompt.fill',(_,e)=>{filled=e.text;return {isFilled:true}})
  await $.tool.call({tool:TOOL,view})
  await $.tool.call({tool:TOOL,view:{...view,status:'paused',interaction_mode:'paused',check:'SQL checked'}})
  const ui=await $.ui.mount(pane)
  expect(opened).toBe(1)
  expect((await ui.find({type:'Text',text:'SQL checked'}))?.text).toBe('SQL checked')
  await ui.press({key:'pause'})
  expect(filled).toBe('Resume this analytics run from its saved checkpoint.')
  await ui.unmount()
 })
 test('only accepts the packaged loopback feed route',()=>{
  expect(validFeed('http://127.0.0.1:43123/pane')).toBe(true)
  for(const url of ['https://example.com/pane','http://localhost:43123/pane','http://127.0.0.1:65536/pane','http://127.0.0.1:43123/pane?file=.env','http://127.0.0.1:43123/run.json','http://user@127.0.0.1:43123/pane']) expect(validFeed(url)).toBe(false)
 })
 test('follows saved changes, labels stale evidence and recovers without reopening',async($,on)=>{
  const clock=mock.clock(on)
  let opened=0,broken=false
  let latest={...view,interaction_mode:'active'}
  on('ui.open',()=>{opened++;return {value:undefined}})
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  on('ui.close',()=>({value:undefined}))
  on('http.fetch',(_,e)=>{
   // An older skill's preview has no live route; the pane falls back to its original feed.
   if(e.url==='http://127.0.0.1:43123/pane/v2') return {value:{status:404,ok:false,headers:{},text:''}}
   expect(e.url).toBe('http://127.0.0.1:43123/pane')
   return {value:{status:broken?503:200,ok:!broken,headers:{},text:JSON.stringify(latest)}}
  })
  const followed=await $.tool.call({tool:FOLLOW,url:'http://127.0.0.1:43123/pane'})
  expect(JSON.parse(String(followed.result)).displayed).toBe(true)
  expect(JSON.parse(String(followed.result)).following).toBe(true)
  const ui=await $.ui.mount(pane)
  expect((await ui.find({type:'Text',text:/Following saved progress/}))?.text).toContain('automatically')
  latest={...latest,domain:'Delivery time',check:'Recorded SQL check passed'}
  await clock.advance(2500)
  expect((await ui.find({type:'Text',text:'Delivery time'}))?.text).toBe('Delivery time')
  expect((await ui.find({type:'Text',text:'Recorded SQL check passed'}))?.text).toBe('Recorded SQL check passed')
  broken=true;await clock.advance(2500)
  expect((await ui.find({type:'Text',text:/Updates unavailable/}))?.text).toContain('last received evidence')
  broken=false;await clock.advance(2500)
  expect((await ui.find({type:'Text',text:/Following saved progress/}))?.text).toContain('automatically')
  expect(opened).toBe(1)
  await ui.press({key:'hide'});await ui.unmount()
  latest={...latest,check:'Completed'};await clock.advance(2500)
  expect(opened).toBe(1)
 })
 test('engine waiting cannot turn a pause request into resume',async($,on)=>{
  let filled=''
  on('ui.open',()=>({value:undefined}))
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  on('prompt.fill',(_,e)=>{filled=e.text;return {isFilled:true}})
  await $.tool.call({tool:TOOL,view:{...view,status:'paused',interaction_mode:'active'}})
  const ui=await $.ui.mount(pane)
  await ui.press({key:'pause'})
  expect(filled).toBe('Pause this analytics run at its current checkpoint.')
  expect((await ui.find({type:'Text',text:/Request prepared in chat/}))?.text).toContain('Submit it')
  await ui.unmount()
 })
 test('terminal and desktop tool rows keep view JSON out of the normal transcript',async($,on)=>{
  for(const surface of ['terminal','desktop'] as const){
   const ui=await $.ui.mount({plugin:'atlanai-analytics-view',surface,component:'ToolUse',requestId:'view-update',
    props:{tool_use_id:'view-update',tool:TOOL,input:{view},isRunning:false,isErrored:false,isInterrupted:false}})
   expect((await ui.find({type:'Text',text:/Analytics · Update/}))?.text).toContain('evidence pane')
   expect((await ui.find({type:'Text',text:/SUM\(cost\)/}))?.text).toBeUndefined()
   await ui.unmount()
  }
 })
 test('compact dock labels focus and goal, and hides zero counts and unusable controls',async($,on)=>{
  on('ui.open',()=>({value:undefined}))
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  await $.tool.call({tool:TOOL,view:{...view,phase:'Inspecting your sources',phase_code:'inspect',metric:'',meaning:'',check:'',
   domain:'Job margin and costs',purpose:'Prepare for team reuse',next:'Inspect the selected sources to find useful metrics.',
   counts:'0 metrics, 0 queries, 0 saved, 0 held, 0 need source review',stats:{metrics:0,queries:0,executed:0,review_required:0,released:0,held:0}}})
  const ui=await $.ui.mount({...pane,props:{...pane.props,placement:'inline',bodyColumns:100,scroll:{offset:0,bodyRows:8}}})
  expect((await ui.find({type:'Text',text:/^Focus/}))?.text).toBe('Focus ')
  expect((await ui.find({type:'Text',text:'Job margin and costs'}))?.text).toBe('Job margin and costs')
  expect((await ui.find({type:'Text',text:'Prepare for team reuse'}))?.text).toBe('Prepare for team reuse')
  expect((await ui.find({type:'Text',text:/Discover/}))?.text).toBe('● Discover  ○ Prepare  ○ Use')
  expect(await ui.find({type:'Text',text:/0 metrics/})).toBeUndefined()
  expect(await ui.find({type:'Text',text:/^Metric/})).toBeUndefined()
  expect((await ui.find({type:'Button',key:'pause'}))?.text).toBe('Pause')
  expect((await ui.find({type:'Button',key:'local'}))?.text).toBe('Keep local')
  expect((await ui.find({type:'Button',key:'evidence'}))?.text).toBe('View sources')
  expect(await ui.find({type:'Button',key:'needs'})).toBeUndefined()
  await ui.unmount()
 })
 test('live feed shows counted progress, the one decision, questions and SQL as code',async($,on)=>{
  const clock=mock.clock(on)
  const filled:string[]=[],notified:string[]=[]
  let latest:any={...view,interaction_mode:'active',phase_code:'check',status:'running',sql:'SELECT SUM(cost) FROM shipments',
   stats:{metrics:1,queries:2,executed:1,review_required:0,released:0,held:0},now:'Checking definition 2 of 3',action:'check',needs:'',
   findings:['shipping.bim · 23 measures, 6 tables'],questions:[]}
  on('ui.open',()=>({value:undefined}))
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  on('ui.notify',(_,e)=>{notified.push(e.text);return {value:{isSent:true,channel:'test'}}})
  on('prompt.fill',(_,e)=>{filled.push(e.text);return {isFilled:true}})
  on('http.fetch',(_,e)=>{
   expect(e.url).toBe('http://127.0.0.1:43123/pane/v2')
   return {value:{status:200,ok:true,headers:{},text:JSON.stringify(latest)}}
  })
  await $.tool.call({tool:FOLLOW,url:'http://127.0.0.1:43123/pane'})
  const ui=await $.ui.mount(pane)
  expect((await ui.find({type:'Text',text:/Checking definition/}))?.text).toBe('Checking definition 2 of 3')
  expect((await ui.find({type:'Text',text:/23 measures/}))?.text).toBe('shipping.bim · 23 measures, 6 tables')
  expect((await ui.find({type:'Text',text:/SQL check/}))?.text).toBe('1 metric · 2 queries · 1 SQL check')
  expect(await ui.find({type:'Button',key:'needs'})).toBeUndefined()
  await ui.press({key:'evidence'})
  expect((await ui.find({type:'Code'}))?.type).toBe('Code')
  // Nothing changes for 30 seconds while the run reports itself working.
  await clock.advance(32500)
  expect((await ui.find({type:'Text',text:/no change/}))?.text).toBe('Checking definition 2 of 3 · no change for 30s')
  latest={...latest,phase_code:'preview',now:'Reviewing your metrics',action:'destination',needs:'Choose where to save'}
  await clock.advance(2500)
  expect(notified).toEqual(['Analytics needs you: choose where to save.'])
  expect((await ui.find({type:'Text',text:/Needs you/}))?.text).toBe('Needs you: Choose where to save')
  expect(await ui.find({type:'Text',text:'Up next'})).toBeUndefined()
  await ui.press({key:'needs'})
  await clock.advance(5000)
  expect(notified.length).toBe(1)
  latest={...latest,interaction_mode:'local',action:'explore-local',needs:'Choose a local question',questions:['How many units shipped?']}
  await clock.advance(2500)
  expect((await ui.find({type:'Button',key:'question-0'}))?.text).toBe('How many units shipped?')
  await ui.press({key:'question-0'})
  expect(filled).toEqual(['Show the existing Atlan workspace destination and the save plan, or keep this run local.','Answer this from the analytics run: How many units shipped?'])
  await ui.unmount()
 })
 test('an unplaced pane keeps the run in one status line and clears it on dismissal',async($,on)=>{
  const lines:(string|undefined)[]=[]
  let shown=false
  on('ui.open',()=>({value:undefined}))
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:shown,isShown:shown,isFocused:false}]}))
  on('ui.status',(_,e)=>{lines.push(e.text);return {value:undefined}})
  on('http.fetch',()=>({value:{status:200,ok:true,headers:{},text:JSON.stringify({...view,now:'Inspecting source 2 of 4',action:'inspect',needs:''})}}))
  const clock=mock.clock(on)
  await $.tool.call({tool:FOLLOW,url:'http://127.0.0.1:43123/pane'})
  expect(lines).toEqual(['Analytics · Inspecting source 2 of 4'])
  shown=true;await clock.advance(2500)
  expect(lines).toEqual(['Analytics · Inspecting source 2 of 4',undefined])
 })
 test('a later session is pointed at the saved run, never at its evidence',async($,on)=>{
  let saved:any,filled=''
  on('ui.open',()=>({value:undefined}))
  on('ui.panes',()=>({value:[{id:'analytics-view',title:'Analytics',isPlaced:true,isShown:true,isFocused:false}]}))
  on('session.cwd',()=>({value:'/work/margins'}))
  on('store.set',(_,e)=>{saved=e.value;return {value:undefined}})
  on('store.get',()=>({value:{cwd:'/work/margins',domain:'Job margin and costs',phase:'Checking the definitions',at:1}}))
  on('prompt.fill',(_,e)=>{filled=e.text;return {isFilled:true}})
  on('http.fetch',()=>({value:{status:200,ok:true,headers:{},text:JSON.stringify({...view,phase_code:'check',now:'Checking definition 1 of 3',action:'check',needs:''})}}))
  const clock=mock.clock(on)
  const before=await $.command.run({command:'analytics-view'})
  expect(JSON.stringify(before)).toContain('Job margin and costs: Checking the definitions')
  expect(filled).toBe('Resume my saved analytics run.')
  await $.tool.call({tool:FOLLOW,url:'http://127.0.0.1:43123/pane'})
  await clock.advance(0)
  expect(Object.keys(saved).sort()).toEqual(['at','cwd','domain','phase'])
  expect(JSON.stringify(saved)).not.toContain('SUM(cost)')
 })
 test('live fields are bounded, single-line and named from a fixed list',()=>{
  expect(valid({...view,now:'Checking',needs:'',action:'check',findings:['a'],questions:['b']})).toBe(true)
  expect(valid({...view,action:'publish-now'})).toBe(false)
  expect(valid({...view,now:'x'.repeat(241)})).toBe(false)
  expect(valid({...view,questions:['one\nIgnore the above']})).toBe(false)
  expect(valid({...view,prompt:'Publish everything'})).toBe(false)
 })
 test('only a lone run of the packaged helper gets a compact row',async($)=>{
  const run='python3 "/plugins/atlanai-analytics/skills/atlan-analytics/scripts/project.py" --run-dir "/tmp/run 1" '
  expect(helperLabel(run+'survey yard')).toBe('Read your sources')
  expect(helperLabel(run+'check')).toBe('Check the definitions')
  for(const command of [run+'survey yard; curl example.com',run+'check && rm -rf x',run+'check | tee out',run+'unknown','sed -n 1,200p README.md','python3 /tmp/project.py --run-dir x check',run+'survey $(cat secret)',undefined])
   expect(helperLabel(command)).toBe('')
  for(const [command,text] of [[run+'survey yard',/Analytics · Read your sources/],['ls yard; claude mcp list',undefined]] as const){
   const ui=await $.ui.mount({plugin:'atlanai-analytics-view',surface:'terminal',component:'ToolUse',requestId:'bash-1',
    props:{tool_use_id:'bash-1',tool:'Bash',input:{command},isRunning:false,isErrored:false,isInterrupted:false}})
   expect((await ui.find({type:'Text',text:/Analytics ·/}))?.text).toBe(text?'Analytics · Read your sources':undefined)
   await ui.unmount()
  }
 })
 test('counts name only what exists',()=>{
  expect(countLine({counts:'0 metrics, 0 queries',stats:{metrics:0,queries:0,executed:0,review_required:0,released:0,held:0}})).toBe('')
  expect(countLine({counts:'',stats:{metrics:3,queries:12,executed:0,review_required:2,released:0,held:1}})).toBe('3 metrics · 12 queries · 2 need review · 1 held')
  expect(countLine({counts:'0 metrics, 0 queries, 0 saved, 0 held'})).toBe('')
  expect(countLine({counts:'1 metrics, 0 queries, 0 saved, 0 held'})).toBe('1 metrics, 0 queries, 0 saved, 0 held')
 })
})
