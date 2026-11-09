import type { Register } from 'claude-code'

const ID = 'analytics-view'
const TOOL = 'mcp__atlanai-analytics-view__show'
const FOLLOW = 'mcp__atlanai-analytics-view__follow'
const fields = ['phase', 'status', 'summary', 'domain', 'purpose', 'counts', 'metric', 'meaning', 'calculation', 'population', 'source', 'check', 'next', 'snapshot_at']
const lists = ['sources', 'opportunities', 'warnings']
const evidenceFields = ['sql', 'evidence', 'usable', 'attention']
const statKeys = ['metrics','queries','executed','review_required','released','held']

// Reject rather than truncate malformed updates. Source text is always rendered as Text.
export function valid(value: any): boolean {
  return !!value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).every(k => fields.includes(k) || lists.includes(k) || evidenceFields.includes(k) || ['interaction_mode','stats','phase_code'].includes(k))
    && (value.phase_code === undefined || ['inspect','scope','build','check','preview','publish','ready'].includes(value.phase_code))
    && (value.stats === undefined || (value.stats && typeof value.stats === 'object' && !Array.isArray(value.stats)
      && Object.keys(value.stats).every(k=>statKeys.includes(k))
      && statKeys.every(k=>Number.isInteger(value.stats[k]) && value.stats[k]>=0 && value.stats[k]<=10000000)))
    && (value.interaction_mode === undefined || ['active', 'paused', 'local'].includes(value.interaction_mode))
    && evidenceFields.every(k => value[k] === undefined || (typeof value[k] === 'string' && value[k].length <= 2000))
    && fields.every(k => typeof value[k] === 'string' && value[k].length <= 2000)
    && lists.every(k => Array.isArray(value[k]) && value[k].length <= 8 && value[k].every((s: any) => typeof s === 'string' && s.length <= 240))
    && JSON.stringify(value).length <= 16000
}

// Only the exact read-only feed printed by the packaged local preview is supported.
export function validFeed(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const match = /^http:\/\/127\.0\.0\.1:([1-9][0-9]{0,4})\/pane$/.exec(value)
  return !!match && Number(match[1]) <= 65535
}

export const register: Register = on => {
  let current: any = null
  let dismissed = false
  let placed = false
  let details = false
  let sources = false
  let feedback = ''
  let feed = ''
  let generation = 0
  let timer: {cancel: () => void} | null = null
  let refreshing = false
  let stale = false
  let attemptedPlacement = false
  on('session.start', async ($, e, next) => {
    await $.tool.register({name:'follow', description:'Follow the saved Analytics run in the native pane. Start the packaged experience_view.py preview first, then pass its exact pane_url. Updates automatically; read-only; respects dismissal. Never pass an arbitrary service URL.',
      inputSchema:{type:'object',properties:{url:{type:'string',maxLength:40}},required:['url'],additionalProperties:false}})
    await $.tool.register({name:'show', description:'Display or update the Analytics source overview, selected metric and saved progress in the native side pane. Pass the exact pane payload from project.py experience --format panel. Presentation only; does not execute or publish. Respects dismissal.',
      inputSchema:{type:'object', properties:{view:{type:'object', properties:{...Object.fromEntries([...[...fields,...evidenceFields].map(k=>[k,{type:'string',maxLength:2000}]),...lists.map(k=>[k,{type:'array',maxItems:8,items:{type:'string',maxLength:240}}])]),phase_code:{type:'string',enum:['inspect','scope','build','check','preview','publish','ready']},stats:{type:'object',properties:Object.fromEntries(statKeys.map(k=>[k,{type:'integer',minimum:0,maximum:10000000}])),required:statKeys,additionalProperties:false},interaction_mode:{type:'string',enum:['active','paused','local']}},required:[...fields,...lists],additionalProperties:false}}, required:['view'],additionalProperties:false}})
    await $.command.register({name:ID,description:'Reopen the Analytics evidence pane',immediate:true})
    return next(e)
  }).catch(($,e,next)=>next(e))
  on('tool.call',{tool:FOLLOW},async ($,e) => {
    if (!validFeed(e.url)) return {result:'Use the exact pane_url from the packaged local Analytics preview.',isError:true}
    timer?.cancel(); timer=null; generation++
    if(feed!==e.url) current=null
    feed=e.url; attemptedPlacement=placed
    const version=generation
    const target=feed
    const refresh = async () => {
      if (refreshing) return false
      refreshing = true
      try {
        const response = await $.http.fetch(target)
        if (version !== generation) return false
        if (!response.ok || response.text.length > 16000) throw new Error('Unavailable')
        const value = JSON.parse(response.text)
        if (!valid(value)) throw new Error('Invalid')
        const changed=stale||!current||JSON.stringify({...current,snapshot_at:''})!==JSON.stringify({...value,snapshot_at:''})
        current = value
        stale = false
        if(!dismissed&&!placed&&!attemptedPlacement){attemptedPlacement=true;const opened=await $.ui.open({id:ID,title:'Analytics'});placed=opened.isPlaced}
        if(changed) $.ui.invalidate('ui.render')
        return true
      } catch {
        if (version === generation) {stale = true; $.ui.invalidate('ui.render')}
        return false
      } finally {refreshing = false}
    }
    const loaded=await refresh()
    timer=$.clock.every(2500,()=>{void refresh()})
    return {result:JSON.stringify({displayed:loaded&&placed&&!dismissed,following:loaded,dismissed,read_only:true}),isError:!loaded}
  }).catch(()=>({result:'The Analytics preview is unavailable. Keep working in chat and restart the owned preview if it stopped.',isError:true}))
  on('tool.call',{tool:TOOL},async ($,e) => {
    if(!valid(e.view)) return {result:'Invalid Analytics view. Preserve the prior pane and refresh its bounded payload.',isError:true}
    // A legacy snapshot must not overwrite an authoritative live subscription.
    if(feed) return {result:'Analytics already follows saved run state automatically. Save progress in the run.'}
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
  for (const component of ['ToolUse','ToolResult'] as const) {
    on('ui.render',{component},($,e,next)=>{
      if((e.component!=='ToolUse'&&e.component!=='ToolResult')||![TOOL,FOLLOW].includes(e.props.tool)||e.props.isExpanded) return next(e)
      const {Text}=$.ui.resolve(e)
      return <Text>{e.props.isErrored?'Analytics view unavailable; continue in chat.':e.component==='ToolUse'?'Analytics · Update the evidence pane':'Analytics evidence remains available in the pane and chat.'}</Text>
    }).catch(($,e,next)=>next(e))
  }
  on('ui.render',{component:'Pane',requestId:ID},($,e,next)=>{
    if(e.component!=='Pane'||!current)return next(e)
    const {Box,Text,Button}=$.ui.resolve(e)
    const v=current
    const paused=v.interaction_mode==='paused'
    const draft=async(text:string)=>{
      try {const result=await $.prompt.fill({text});feedback=result.isFilled?'Request prepared in chat. Submit it to apply your choice.':'Could not prepare the request. Type your choice in chat.'}
      catch {feedback='Could not prepare the request. Type your choice in chat.'}
      $.ui.invalidate('ui.render')
    }
    if(e.surface==='desktop'){
      // Use the host's typography, colors, borders and buttons. A scaled image
      // makes ordinary text oversized and cannot follow the user's host theme.
      const stats=v.stats||{}
      const count=(key:string)=>Number.isInteger(stats[key])?String(stats[key]):'—'
      const local=v.interaction_mode==='local'
      const active=['preview','publish','ready'].includes(v.phase_code)?2:v.metric?1:0
      const meaning=v.meaning.match(/^.*?[.!?](?:\s|$)/)?.[0].trim()||v.meaning
      return <Box flexDirection="column" gap={1}>
        <Box flexDirection="row" justifyContent="space-between" gap={1}>
          <Text bold>{v.domain||'Your analytics'}</Text>
          <Text dimColor>{stale?'Updates unavailable':feed?'Live':'Saved snapshot'}</Text>
        </Box>
        <Text dimColor>{v.purpose||'Start with what matters to you'}</Text>
        <Box flexDirection="row" gap={2} flexWrap="wrap">
          {['Discover','Prepare','Use'].map((label,i)=><Text key={label} bold={i===active} dimColor={i!==active}>{(i<active?'✓ ':i===active?'● ':'○ ')+label}</Text>)}
        </Box>
        <Box flexDirection="row" gap={1} flexWrap="wrap">
          {[['metrics','Metrics'],['queries','Queries'],['executed','SQL checks']].map(([key,label])=><Box key={key} flexDirection="column" flexGrow={1} borderStyle="round" borderDimColor paddingX={1} paddingY={1}><Text bold>{count(key)}</Text><Text dimColor>{label}</Text></Box>)}
        </Box>
        <Box flexDirection="column" gap={1} borderStyle="round" borderDimColor padding={1}>
          <Box flexDirection="row" justifyContent="space-between" gap={1} flexWrap="wrap"><Text bold>{v.metric||'What matters to you?'}</Text><Text dimColor>{local?'Saved locally':Number(stats.released)>0?'Shared in Atlan':'In progress'}</Text></Box>
          <Text>{v.metric?meaning:(v.summary||'Choose a business focus in chat. Your first metric will appear here.')}</Text>
          <Text dimColor>{v.check||'Waiting for your business focus'}</Text>
          <Button key="evidence" label={details?'Hide definition and checks':'View definition and checks'} onPress={()=>{details=!details;$.ui.invalidate('ui.render')}}/>
        </Box>
        {details?<Box flexDirection="column" gap={1}><Text bold>{v.metric+' · Evidence'}</Text><Text>{v.meaning}</Text><Text>{'Calculation: '+v.calculation}</Text><Text>{'Includes: '+v.population}</Text><Text>{v.source}</Text><Text>{v.evidence||v.check}</Text>{v.sql?<Text>{'Saved SQL: '+v.sql}</Text>:''}<Text dimColor>Saved checks are not fresh execution or proof of dashboard parity.</Text></Box>:''}
        <Box flexDirection="row" justifyContent="space-between" alignItems="center" gap={1} flexWrap="wrap">
          <Text>{stats.review_required?`${stats.review_required} queries need source review`:v.metric?'Explore your saved definitions':'Understanding your sources'}</Text>
          <Button key="ask" label={stats.review_required?'Review queries':'Explore a question'} onPress={()=>void draft(stats.review_required?'Show the queries that still need source review and the evidence needed to review them.':'Show a question supported by this analytics run, with its local or released scope.')}/>
        </Box>
        <Box flexDirection="row" flexWrap="wrap" gap={1}>
          <Button key="sources" label={sources?'Hide sources':'View sources'} dimColor onPress={()=>{sources=!sources;$.ui.invalidate('ui.render')}}/>
          {!local?<Button key="pause" label={paused?'Resume…':'Pause…'} dimColor onPress={()=>void draft(paused?'Resume this analytics run from its saved checkpoint.':'Pause this analytics run at its current checkpoint.')}/>:''}
          {!local?<Button key="local" label="Keep local…" dimColor onPress={()=>void draft('Keep this analytics run local. Do not publish its definitions.')}/>:''}
        </Box>
        {sources?<Box flexDirection="column" gap={1}><Text bold>What we found</Text><Text>{v.summary||v.sources.join(', ')}</Text>{v.opportunities.map((s:string)=><Text key={s}>{s}</Text>)}</Box>:''}
        {v.warnings.map((s:string)=><Text key={s}>{s}</Text>)}
        {stale?<Text>Updates unavailable. Showing the last received evidence.</Text>:''}
        <Text dimColor>{`${count('released')} shared in Atlan · ${count('held')} held`}</Text>
        <Text dimColor>{feedback||'Actions prepare a message in chat. Submit it to continue.'}</Text>
      </Box>
    }
    // Inline terminal panes may expose only a few rows. Keep the current task
    // visible there; the full evidence is an explicit expansion, not a scroll hunt.
    if(e.surface==='terminal'&&e.props.scroll.bodyRows<18&&!details){
      const width=Math.max(30,e.props.bodyColumns-2)
      const line=(value:string)=>value.length>width?value.slice(0,width-1)+'…':value
      return <Box flexDirection="column" gap={0}>
        <Text bold>{line(v.phase+(stale?' · Updates unavailable':''))}</Text>
        <Text>{line(v.domain?(v.domain+(v.purpose?' · '+v.purpose:'')):(v.summary||'Inspecting the selected sources'))}</Text>
        <Text bold>{line(v.metric?(v.metric+' · '+v.check):v.next)}</Text>
        {v.metric?<Text>{line(v.next)}</Text>:''}
        <Text dimColor>{line(v.counts)}</Text>
        <Box flexDirection="row" gap={1}>
          <Button key="evidence" label="Expand evidence" onPress={()=>{details=true;$.ui.invalidate('ui.render')}}/>
          <Button key="pause" label={paused?'Prepare resume':'Prepare pause'} onPress={()=>void draft(paused?'Resume this analytics run from its saved checkpoint.':'Pause this analytics run at its current checkpoint.')}/>
          <Button key="local" label="Prepare keep local" onPress={()=>void draft('Keep this analytics run local. Do not publish its definitions.')}/>
        </Box>
        <Text dimColor>{line(feedback||(stale?'Last received evidence; continue in chat.':feed?'Live updates · buttons prepare a chat request; submit to apply.':'Saved snapshot · buttons prepare a chat request.'))}</Text>
      </Box>
    }
    return <Box flexDirection="column" gap={1}>
      <Text bold>{v.phase}</Text><Text dimColor>{v.status+' · '+v.snapshot_at}</Text>
      {e.surface==='terminal'&&e.props.scroll.bodyRows<18?<Button key="compact" label="Back to progress" onPress={()=>{details=false;$.ui.invalidate('ui.render')}}/>:''}
      <Text dimColor>{stale?'Updates unavailable. Showing the last received evidence.':feed?'Following saved progress automatically.':'Saved snapshot; automatic updates are not connected.'}</Text>
      <Text bold>What we found</Text><Text>{v.summary || v.sources.join(', ')}</Text>
      {v.opportunities.map((s:string)=><Text key={s}>{'• '+s}</Text>)}
      <Text bold>What matters to you</Text><Text>{v.domain||'Choose your business focus in chat'}</Text><Text dimColor>{v.purpose||'Then choose what you want to accomplish'}</Text>
      <Text bold>{v.metric||'Your first metric will appear here'}</Text><Text>{v.meaning}</Text><Text>{v.check}</Text>
      {v.metric?<Text>{'Calculation: '+v.calculation}</Text>:''}
      {v.population?<Text>{'Includes: '+v.population}</Text>:''}
      {v.metric?<Button key="evidence" label={details?'Hide evidence':'Inspect source and checks'} onPress={()=>{details=!details;$.ui.invalidate('ui.render')}}/>:''}
      {details?<Text>{v.source+'\nSaved checks are not fresh execution or proof of dashboard parity.'}</Text>:''}
      {details&&v.evidence?<Text>{v.evidence}</Text>:''}
      {details&&v.sql?<Text>{'Saved SQL: '+v.sql}</Text>:''}
      <Text bold>{v.counts}</Text>{v.warnings.map((s:string)=><Text key={s}>{s}</Text>)}
      {v.usable?<Text bold>Available now</Text>:''}{v.usable?<Text>{v.usable}</Text>:''}
      {v.attention?<Text bold>Needs attention</Text>:''}{v.attention?<Text>{v.attention}</Text>:''}
      <Text bold>Up next</Text><Text>{v.next}</Text>
      <Button key="details" label="Ask for details" onPress={()=>void draft('Show the selected analytics metric, its SQL, source and checks.')}/>
      <Button key="pause" label={paused?'Prepare resume request':'Prepare pause request'} onPress={()=>void draft(paused?'Resume this analytics run from its saved checkpoint.':'Pause this analytics run at its current checkpoint.')}/>
      <Button key="local" label="Prepare keep-local request" onPress={()=>void draft('Keep this analytics run local. Do not publish its definitions.')}/>
      <Button key="hide" label="Hide pane" onPress={()=>{dismissed=true;placed=false;void $.ui.close({id:ID})}}/>
      <Text dimColor>Buttons fill the prompt. Submit it to make your choice.</Text>
      {feedback?<Text>{feedback}</Text>:''}
    </Box>
  }).catch(($,e,next)=>next(e))
}
