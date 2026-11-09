import {describe,expect,test} from 'claude-code/testing'
import {valid} from '../hooks/register'
const TOOL='mcp__atlanai-analytics-view__show'
const view={phase:'Checking definitions',status:'running',summary:'Synthetic shipping model and query history.',domain:'Shipping cost',purpose:'Prepare reusable metrics',counts:'1 metrics, 0 queries, 0 saved, 0 held',metric:'Shipping cost',meaning:'Cost for shipped shipments.',calculation:'SUM(cost)',population:'Shipped shipments only',source:'Synthetic model',check:'SQL untested',next:'Check the definition.',snapshot_at:'2026-11-09T00:00:00Z',sources:['shipping.bim'],opportunities:['Cost by region'],warnings:[]}
const pane={plugin:'atlanai-analytics-view',surface:'terminal',component:'Pane',requestId:'analytics-view',props:{title:'Analytics',isFocused:false,bodyColumns:60,placement:'dock',scroll:{offset:0,bodyRows:40},view:{}}} as const
describe('Analytics native pane',()=>{
 test('validates bounded text without commands',()=>{
  expect(valid(view)).toBe(true);expect(valid({...view,command:'publish'})).toBe(false)
  expect(valid({...view,summary:'x'.repeat(2001)})).toBe(false)
 })
 test('opens, draws, updates and preserves dismissal',async($,on)=>{
  let opened=0
  on('ui.open',()=>{opened++;return {value:{isPlaced:true}}})
  on('ui.close',()=>({value:undefined}))
  await $.tool.call({tool:TOOL,view})
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
  on('ui.open',()=>({value:{isPlaced:false}}))
  const result=await $.tool.call({tool:TOOL,view})
  expect(JSON.stringify(result)).toContain('text_fallback')
 })
 test('buttons reveal evidence and fill requests without executing them',async($,on)=>{
  const filled:string[]=[]
  on('ui.open',()=>({value:{isPlaced:true}}))
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
  on('ui.open',()=>{opened++;return {value:{isPlaced:true}}})
  on('prompt.fill',(_,e)=>{filled=e.text;return {isFilled:true}})
  await $.tool.call({tool:TOOL,view})
  await $.tool.call({tool:TOOL,view:{...view,status:'paused',check:'SQL checked'}})
  const ui=await $.ui.mount(pane)
  expect(opened).toBe(1)
  expect((await ui.find({type:'Text',text:'SQL checked'}))?.text).toBe('SQL checked')
  await ui.press({key:'pause'})
  expect(filled).toBe('Resume this analytics run from its saved checkpoint.')
  await ui.unmount()
 })
})
