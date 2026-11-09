import type { Register } from 'claude-code'

const ID = 'analytics-view'
const TOOL = 'mcp__atlanai-analytics-view__show'
const fields = ['phase', 'status', 'summary', 'domain', 'purpose', 'counts', 'metric', 'meaning', 'calculation', 'population', 'source', 'check', 'next', 'snapshot_at']
const lists = ['sources', 'opportunities', 'warnings']

// Reject rather than truncate malformed updates. Source text is always rendered as Text.
export function valid(value: any): boolean {
  return !!value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).every(k => fields.includes(k) || lists.includes(k))
    && fields.every(k => typeof value[k] === 'string' && value[k].length <= 2000)
    && lists.every(k => Array.isArray(value[k]) && value[k].length <= 8 && value[k].every((s: any) => typeof s === 'string' && s.length <= 240))
    && JSON.stringify(value).length <= 16000
}

export const register: Register = on => {
  let current: any = null
  let dismissed = false
  let placed = false
  let details = false
  on('session.start', async ($, e, next) => {
    await $.tool.register({name:'show', description:'Display or update the Analytics source overview, selected metric and saved progress in the native side pane. Pass the exact pane payload from project.py experience --format panel. Presentation only; does not execute or publish. Respects dismissal.',
      inputSchema:{type:'object', properties:{view:{type:'object', properties:Object.fromEntries([...fields.map(k=>[k,{type:'string',maxLength:2000}]),...lists.map(k=>[k,{type:'array',maxItems:8,items:{type:'string',maxLength:240}}])]),required:[...fields,...lists],additionalProperties:false}}, required:['view'],additionalProperties:false}})
    await $.command.register({name:ID,description:'Reopen the Analytics evidence pane',immediate:true})
    return next(e)
  }).catch(($,e,next)=>next(e))
  on('tool.call',{tool:TOOL},async ($,e) => {
    if(!valid(e.view)) return {result:'Invalid Analytics view. Preserve the prior pane and refresh its bounded payload.',isError:true}
    current=e.view
    if(!dismissed&&!placed){const opened=await $.ui.open({id:ID,title:'Analytics'});placed=opened.isPlaced}
    $.ui.invalidate('ui.render')
    return {result:JSON.stringify({displayed:placed&&!dismissed,surface:placed?'native_pane':'text_fallback',dismissed,read_only:true})}
  }).catch(()=>({result:JSON.stringify({displayed:false,surface:'text_fallback',read_only:true}),isError:true}))
  on('command.run',{command:ID},async ($) => {
    if(!current) return {text:'Start Analytics setup to inspect a source and prepare its view.'}
    dismissed=false
    const opened=await $.ui.open({id:ID,title:'Analytics',focus:true});placed=opened.isPlaced
    $.ui.invalidate('ui.render')
    return {text:placed?'Analytics pane opened.':'The pane needs a wider window. The evidence remains available in chat.'}
  }).catch(()=>({text:'The Analytics pane is unavailable. Continue with the evidence in chat.'}))
  on('ui.close',{id:ID},($,e,next)=>{dismissed=true;placed=false;return next(e)}).catch(($,e,next)=>next(e))
  on('ui.render',{component:'Pane',requestId:ID},($,e,next)=>{
    if(e.component!=='Pane'||!current)return next(e)
    const {Box,Text,Button}=$.ui.resolve(e)
    const v=current
    return <Box flexDirection="column" gap={1}>
      <Text bold>{v.phase}</Text><Text dimColor>{v.status+' · '+v.snapshot_at}</Text>
      <Text bold>What we found</Text><Text>{v.summary || v.sources.join(', ')}</Text>
      {v.opportunities.map((s:string)=><Text key={s}>{'• '+s}</Text>)}
      <Text bold>What matters to you</Text><Text>{v.domain||'Choose your business focus in chat'}</Text><Text dimColor>{v.purpose||'Then choose what you want to accomplish'}</Text>
      <Text bold>{v.metric||'Your first metric will appear here'}</Text><Text>{v.meaning}</Text><Text>{v.check}</Text>
      {v.metric?<Text>{'Calculation: '+v.calculation}</Text>:''}
      {v.population?<Text>{'Includes: '+v.population}</Text>:''}
      {v.metric?<Button key="evidence" label={details?'Hide evidence':'Inspect source and checks'} onPress={()=>{details=!details;$.ui.invalidate('ui.render')}}/>:''}
      {details?<Text>{v.source+'\nSaved checks are not fresh execution or proof of dashboard parity.'}</Text>:''}
      <Text bold>{v.counts}</Text>{v.warnings.map((s:string)=><Text key={s}>{s}</Text>)}
      <Text bold>Up next</Text><Text>{v.next}</Text>
      <Button key="details" label="Ask for details" onPress={()=>void $.prompt.fill({text:'Show the selected analytics metric, its SQL, source and checks.'})}/>
      <Button key="pause" label={v.status==='paused'?'Resume in chat':'Pause in chat'} onPress={()=>void $.prompt.fill({text:v.status==='paused'?'Resume this analytics run from its saved checkpoint.':'Pause this analytics run at its current checkpoint.'})}/>
      <Button key="local" label="Keep local" onPress={()=>void $.prompt.fill({text:'Keep this analytics run local. Do not publish its definitions.'})}/>
      <Button key="hide" label="Hide pane" onPress={()=>{dismissed=true;placed=false;void $.ui.close({id:ID})}}/>
      <Text dimColor>Buttons fill the prompt. Submit it to make your choice.</Text>
    </Box>
  }).catch(($,e,next)=>next(e))
}
