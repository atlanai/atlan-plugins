import {describe,expect,test,mock} from 'claude-code/testing'
import {valid,validFeed} from '../hooks/register'
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
})
