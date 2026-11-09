import type { Register } from 'claude-code'

const ID = 'analytics-view'
const TOOL = 'mcp__atlanai-analytics-view__show'
const FOLLOW = 'mcp__atlanai-analytics-view__follow'
const LAST = 'last-run'
const fields = ['phase', 'status', 'summary', 'domain', 'purpose', 'counts', 'metric', 'meaning', 'calculation', 'population', 'source', 'check', 'next', 'snapshot_at']
const lists = ['sources', 'opportunities', 'warnings']
const evidenceFields = ['sql', 'evidence', 'usable', 'attention']
const statKeys = ['metrics','queries','executed','review_required','released','held']
// Live detail from the preview's /pane/v2 route. An older skill omits all of it.
const liveFields = ['now', 'needs']
const liveLists = ['findings', 'questions']
const actions = ['', 'resume', 'repair', 'choose-domain', 'choose-purpose', 'select-focus', 'recover-access', 'ask', 'inspect', 'scope', 'build', 'check', 'preview', 'destination', 'publish', 'explore-local']
const MAX = 24000

// What the pane offers when the run waits on the person. The feed names a step; the
// button's label and the text it drafts are fixed here, never taken from the feed.
const NEEDS: Record<string, [string, string]> = {
  'choose-domain': ['Choose focus', 'Help me choose the business area to focus on from these sources.'],
  'choose-purpose': ['Choose goal', 'Help me choose what to accomplish in the business area I selected.'],
  'scope': ['Confirm scope', 'Show what sources are available and what you will use.'],
  'destination': ['Choose where to save', 'Show the existing Atlan workspace destination and the save plan, or keep this run local.'],
  'recover-access': ['Restore access', 'Resume this saved run after checking the existing Atlan connection.'],
  'resume': ['Resume', 'Resume this analytics run from its saved checkpoint.'],
  'ask': ['Ask a question', 'Show me questions supported by the definitions we just saved.'],
  'explore-local': ['Ask a question', 'Show a supported question I can explore with this local dataset and its check limitations.'],
}
// Steps worth a notification: the person may have walked away from a long run.
const NOTIFY: Record<string, string> = {
  'destination': 'Analytics needs you: choose where to save.',
  'recover-access': 'Analytics needs you: restore access to continue.',
  'ask': 'Analytics is ready for questions.',
  'explore-local': 'Analytics is ready for questions on local data.',
  'blocked': 'Analytics needs a repair. Your work is saved.',
}
const COMMANDS: Record<string, string> = {survey:'Read your sources', history:'Read query history', status:'Check saved progress',
  check:'Check the definitions', location:'Check where to save', preview:'Prepare a preview', experience:'Refresh the view',
  journey:'Save your choice', progress:'Save progress', bind:'Connect local data'}

// Reject rather than truncate malformed updates. Source text is always rendered as Text.
export function valid(value: any): boolean {
  return !!value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).every(k => fields.includes(k) || lists.includes(k) || evidenceFields.includes(k) || liveFields.includes(k) || liveLists.includes(k) || ['interaction_mode','stats','phase_code','action'].includes(k))
    && (value.phase_code === undefined || ['inspect','scope','build','check','preview','publish','ready'].includes(value.phase_code))
    && (value.stats === undefined || (value.stats && typeof value.stats === 'object' && !Array.isArray(value.stats)
      && Object.keys(value.stats).every(k=>statKeys.includes(k))
      && statKeys.every(k=>Number.isInteger(value.stats[k]) && value.stats[k]>=0 && value.stats[k]<=10000000)))
    && (value.interaction_mode === undefined || ['active', 'paused', 'local'].includes(value.interaction_mode))
    && (value.action === undefined || actions.includes(value.action))
    && evidenceFields.every(k => value[k] === undefined || (typeof value[k] === 'string' && value[k].length <= 2000))
    && liveFields.every(k => value[k] === undefined || (typeof value[k] === 'string' && value[k].length <= 240 && !/[\n\r]/.test(value[k])))
    && liveLists.every(k => value[k] === undefined || (Array.isArray(value[k]) && value[k].length <= 8 && value[k].every((s: any) => typeof s === 'string' && s.length <= 240 && !/[\n\r]/.test(s))))
    && fields.every(k => typeof value[k] === 'string' && value[k].length <= 2000)
    && lists.every(k => Array.isArray(value[k]) && value[k].length <= 8 && value[k].every((s: any) => typeof s === 'string' && s.length <= 240))
    && JSON.stringify(value).length <= MAX
}

// Only the exact read-only feed printed by the packaged local preview is supported.
export function validFeed(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const match = /^http:\/\/127\.0\.0\.1:([1-9][0-9]{0,4})\/pane$/.exec(value)
  return !!match && Number(match[1]) <= 65535
}

// A compact row replaces a Bash call only when the whole command is one run of the
// packaged project helper. Anything chained, piped or substituted keeps its full row.
export function helperLabel(command: unknown): string {
  if (typeof command !== 'string' || command.length > 2000 || /[;&|`$()<>\n\r\\]/.test(command)) return ''
  const match = /^python3 "?[^"]*\/atlan-analytics\/scripts\/project\.py"? --run-dir (?:"[^"]*"|\S+) ([a-z]+)(?: |$)/.exec(command)
  return match ? COMMANDS[match[1]] || '' : ''
}

// Counts worth reading: zeros during discovery say nothing was found, which is not yet known.
export function countLine(v: any): string {
  if (!v.stats) return /[1-9]/.test(v.counts) ? v.counts : ''
  const labels: [string, string, string][] = [['metrics','metric','metrics'],['queries','query','queries'],['executed','SQL check','SQL checks'],
    ['review_required','needs review','need review'],['released','shared in Atlan','shared in Atlan'],['held','held','held']]
  return labels.filter(([key])=>v.stats[key]>0).map(([key,one,many])=>`${v.stats[key]} ${v.stats[key]===1?one:many}`).join(' · ')
}

type State = {current: any, dismissed: boolean, placed: boolean, feed: string, stale: boolean, idle: number,
  statusLine: string | undefined, remembered: string, cwd: string}

const waiting = (v: any) => !!v.needs && !!NEEDS[v.action]
const working = (v: any) => ['running', 'working'].includes(v.status) && !waiting(v)
const signalOf = (v: any) => v.status === 'blocked' ? 'blocked' : waiting(v) ? v.action : ''
const nowLine = (s: State, v: any) => (v.now || v.phase) + (s.feed && !s.stale && s.idle >= 3 && working(v) ? ` · no change for ${s.idle * 10}s` : '')
const liveLabel = (s: State) => s.stale ? 'Updates unavailable' : s.feed ? 'Live' : 'Saved snapshot'
const step = (v: any) => ['preview','publish','ready'].includes(v.phase_code) ? 2 : v.metric || ['build','check'].includes(v.phase_code) ? 1 : 0
const rail = (v: any) => ['Discover','Prepare','Use'].map((label, i) => (i < step(v) ? '✓ ' : i === step(v) ? '● ' : '○ ') + label)

async function open($: any, s: State, focus = false) {
  await $.ui.open(focus ? {id:ID,title:'Analytics',rows:8,focus:true} : {id:ID,title:'Analytics',rows:8})
  s.placed = (await $.ui.panes()).some((p: any)=>p.id===ID&&p.isPlaced&&p.isShown)
}

// Where no pane is drawn and the person has not closed it, one status line carries the run.
function status($: any, s: State) {
  const want = s.current && s.feed && !s.placed && !s.dismissed && !s.stale
    ? 'Analytics · ' + nowLine(s, s.current) + (waiting(s.current) ? ' · needs you' : '') : undefined
  if (want === s.statusLine) return
  s.statusLine = want
  try { $.ui.status(want) } catch {}
}

async function tell($: any, text: string) {
  try { if ((await $.ui.notify(text, {title:'Analytics'})).isSent) return } catch {}
  try { $.ui.toast(text) } catch {}
}

// Keep a pointer to the run, never its evidence, so a later session here can offer to resume.
async function remember($: any, s: State, v: any) {
  const key = [v.phase_code, v.status, v.domain, v.purpose].join('|')
  if (key === s.remembered) return
  s.remembered = key
  try {
    s.cwd = s.cwd || await $.session.cwd()
    if (v.phase_code === 'ready' && ['complete','partial_ready'].includes(v.status)) await $.store.delete(LAST)
    else await $.store.set(LAST, {cwd: s.cwd, domain: v.domain.slice(0, 160), phase: v.phase.slice(0, 120), at: await $.clock.now()})
  } catch {}
}

async function recall($: any, s: State) {
  try {
    const last: any = await $.store.get(LAST)
    s.cwd = s.cwd || await $.session.cwd()
    const ok = last && typeof last === 'object' && last.cwd === s.cwd && typeof last.domain === 'string' && last.domain.length <= 160
      && typeof last.phase === 'string' && last.phase.length <= 120 && Number.isFinite(last.at)
    return ok ? last : null
  } catch { return null }
}

export const register: Register = on => {
  const s: State = {current:null,dismissed:false,placed:false,feed:'',stale:false,idle:0,statusLine:undefined,remembered:'',cwd:''}
  let details = false
  let sources = false
  let feedback = ''
  let extended = true
  let generation = 0
  let timer: {cancel: () => void} | null = null
  let refreshing = false
  let attemptedPlacement = false
  let changedAt = 0
  let signal = ''

  on('session.start', async ($, e, next) => {
    await $.tool.register({name:'follow', description:'Follow the saved Analytics run in the native pane. Start the packaged experience_view.py preview first, then pass its exact pane_url. Updates automatically; read-only; respects dismissal. Never pass an arbitrary service URL.',
      inputSchema:{type:'object',properties:{url:{type:'string',maxLength:40}},required:['url'],additionalProperties:false}})
    await $.tool.register({name:'show', description:'Display or update the Analytics source overview, selected metric and saved progress in the native side pane. Pass the exact pane payload from project.py experience --format panel. Presentation only; does not execute or publish. Respects dismissal.',
      inputSchema:{type:'object', properties:{view:{type:'object', properties:{...Object.fromEntries([...[...fields,...evidenceFields].map(k=>[k,{type:'string',maxLength:2000}]),...lists.map(k=>[k,{type:'array',maxItems:8,items:{type:'string',maxLength:240}}])]),phase_code:{type:'string',enum:['inspect','scope','build','check','preview','publish','ready']},stats:{type:'object',properties:Object.fromEntries(statKeys.map(k=>[k,{type:'integer',minimum:0,maximum:10000000}])),required:statKeys,additionalProperties:false},interaction_mode:{type:'string',enum:['active','paused','local']}},required:[...fields,...lists],additionalProperties:false}}, required:['view'],additionalProperties:false}})
    await $.command.register({name:ID,description:'Reopen the Analytics evidence pane, or resume a saved run',immediate:true})
    // Say once that an unfinished run is saved in this folder. The run itself is never read here.
    const last = await recall($, s)
    if (last && !last.announced) {
      try { $.ui.toast('A saved Analytics run is here. /analytics-view resumes it.'); await $.store.set(LAST, {...last, announced: true}) } catch {}
    }
    return next(e)
  }).catch(($,e,next)=>next(e))

  on('tool.call',{tool:FOLLOW},async ($,e) => {
    if (!validFeed(e.url)) return {result:'Use the exact pane_url from the packaged local Analytics preview.',isError:true}
    timer?.cancel(); timer=null; generation++
    if(s.feed!==e.url){s.current=null;extended=true;signal='';s.idle=0}
    s.feed=e.url; attemptedPlacement=s.placed
    const version=generation
    const target=s.feed
    const refresh = async () => {
      if (refreshing) return false
      refreshing = true
      try {
        let response = await $.http.fetch(extended ? target + '/v2' : target)
        // A preview from an older skill has no live route: keep its original shape.
        if (extended && response.status === 404) { extended = false; response = await $.http.fetch(target) }
        if (version !== generation) return false
        if (!response.ok || response.text.length > MAX) throw new Error('Unavailable')
        const value = JSON.parse(response.text)
        if (!valid(value)) throw new Error('Invalid')
        const first=!s.current
        const changed=s.stale||first||JSON.stringify({...s.current,snapshot_at:''})!==JSON.stringify({...value,snapshot_at:''})
        const hadMetric=!!s.current?.metric
        s.current = value
        s.stale = false
        const at = await $.clock.now()
        if (changed) changedAt = at
        const quiet = Math.floor((at - changedAt) / 10000)
        const redraw = changed || quiet !== s.idle
        s.idle = quiet
        if(!s.dismissed&&!s.placed&&!attemptedPlacement){attemptedPlacement=true;await open($, s)}
        else if(!s.dismissed&&!s.placed) s.placed=(await $.ui.panes()).some((p: any)=>p.id===ID&&p.isPlaced&&p.isShown)
        const reached = signalOf(value)
        // Notices and the resume pointer never hold up the redraw.
        if (!first && reached !== signal && NOTIFY[reached]) void tell($, NOTIFY[reached])
        else if (!first && !hadMetric && value.metric && !s.placed) { try { $.ui.toast('First metric ready: ' + value.metric.slice(0, 80)) } catch {} }
        signal = reached
        if (changed) void remember($, s, value)
        status($, s)
        if(redraw) $.ui.invalidate('ui.render')
        return true
      } catch {
        if (version === generation) {s.stale = true; status($, s); $.ui.invalidate('ui.render')}
        return false
      } finally {refreshing = false}
    }
    const loaded=await refresh()
    timer=$.clock.every(2500,()=>{void refresh()})
    return {result:JSON.stringify({displayed:loaded&&s.placed&&!s.dismissed,following:loaded,dismissed:s.dismissed,read_only:true}),isError:!loaded}
  }).catch(()=>({result:'The Analytics preview is unavailable. Keep working in chat and restart the owned preview if it stopped.',isError:true}))

  on('tool.call',{tool:TOOL},async ($,e) => {
    if(!valid(e.view)) return {result:'Invalid Analytics view. Preserve the prior pane and refresh its bounded payload.',isError:true}
    // A legacy snapshot must not overwrite an authoritative live subscription.
    if(s.feed) return {result:'Analytics already follows saved run state automatically. Save progress in the run.'}
    s.current=e.view
    if(!s.dismissed&&!s.placed) await open($, s)
    $.ui.invalidate('ui.render')
    return {result:JSON.stringify({displayed:s.placed&&!s.dismissed,surface:s.placed?'native_pane':'text_fallback',dismissed:s.dismissed,read_only:true})}
  }).catch(()=>({result:JSON.stringify({displayed:false,surface:'text_fallback',read_only:true}),isError:true}))

  on('command.run',{command:ID},async ($) => {
    if(!s.current){
      const last = await recall($, s)
      if(!last) return {text:'Start Analytics setup to inspect a source and prepare its view.'}
      let filled = false
      try { filled = (await $.prompt.fill({text:'Resume my saved analytics run.'})).isFilled } catch {}
      return {text:`A saved Analytics run is here${last.domain?' for '+last.domain:''}: ${last.phase}. ${filled?'A resume request is in the prompt. Send it to continue.':'Ask to resume the saved analytics run to continue.'}`}
    }
    s.dismissed=false
    await open($, s, true)
    status($, s)
    $.ui.invalidate('ui.render')
    return {text:s.placed?'Analytics pane opened.':'The pane needs a wider window. The evidence remains available in chat.'}
  }).catch(()=>({text:'The Analytics pane is unavailable. Continue with the evidence in chat.'}))

  on('ui.close',{id:ID},($,e,next)=>{s.dismissed=true;s.placed=false;status($, s);return next(e)}).catch(($,e,next)=>next(e))

  for (const component of ['ToolUse','ToolResult'] as const) {
    on('ui.render',{component},($,e,next)=>{
      if(e.component!=='ToolUse'&&e.component!=='ToolResult') return next(e)
      if(e.props.isExpanded) return next(e)
      const {Text}=$.ui.resolve(e)
      if(e.component==='ToolUse'&&e.props.tool==='Bash'&&!e.props.isErrored){
        const label=helperLabel((e.props.input as any)?.command)
        return label?<Text>{'Analytics · '+label}</Text>:next(e)
      }
      if(![TOOL,FOLLOW].includes(e.props.tool)) return next(e)
      return <Text>{e.props.isErrored?'Analytics view unavailable; continue in chat.':e.component==='ToolUse'?'Analytics · Update the evidence pane':'Analytics evidence remains available in the pane and chat.'}</Text>
    }).catch(($,e,next)=>next(e))
  }

  on('ui.render',{component:'Pane',requestId:ID},($,e,next)=>{
    if(e.component!=='Pane'||!s.current)return next(e)
    const {Box,Text,Button,Code}=$.ui.resolve(e) as any
    const v=s.current
    const paused=v.interaction_mode==='paused'
    const local=v.interaction_mode==='local'
    const draft=async(text:string)=>{
      try {const result=await $.prompt.fill({text});feedback=result.isFilled?'Request prepared in chat. Submit it to apply your choice.':'Could not prepare the request. Type your choice in chat.'}
      catch {feedback='Could not prepare the request. Type your choice in chat.'}
      $.ui.invalidate('ui.render')
    }
    const toggle=(change:()=>void)=>()=>{change();$.ui.invalidate('ui.render')}
    const need=waiting(v)?NEEDS[v.action]:null
    const counted=countLine(v)
    const findings:string[]=v.findings?.length?v.findings:v.sources
    const questions:string[]=v.questions||[]
    const sql=v.sql?(Code?<Code key="sql" source={v.sql} language="sql"/>:<Text>{'Saved SQL: '+v.sql}</Text>):''
    // The one thing the run waits on, as the only primary button in the pane.
    const needButton=need?<Button key="needs" variant="primary" label={need[0]} onPress={()=>void draft(need[1])}/>:''
    const ask=(q:string,i:number)=><Button key={'question-'+i} label={q} onPress={()=>void draft('Answer this from the analytics run: '+q)}/>
    const pause=(label:string,resume:string,dim=false)=><Button key="pause" label={paused?resume:label} dimColor={dim||undefined} onPress={()=>void draft(paused?'Resume this analytics run from its saved checkpoint.':'Pause this analytics run at its current checkpoint.')}/>
    const keepLocal=(label:string,dim=false)=><Button key="local" label={label} dimColor={dim||undefined} onPress={()=>void draft('Keep this analytics run local. Do not publish its definitions.')}/>
    const row=(label:string,value:any,right?:any)=><Box key={label} flexDirection="row" justifyContent="space-between" gap={1}>
      <Box flexDirection="row" gap={1}><Text dimColor>{label.padEnd(6)}</Text>{value}</Box>{right||''}</Box>

    if(e.surface==='desktop'){
      // Use the host's typography, colors, borders and buttons. A scaled image
      // makes ordinary text oversized and cannot follow the user's host theme.
      const stats=v.stats||{}
      const count=(key:string)=>Number.isInteger(stats[key])?String(stats[key]):'—'
      const meaning=v.meaning.match(/^.*?[.!?](?:\s|$)/)?.[0].trim()||v.meaning
      const prepared=Number(stats.metrics)>0||Number(stats.queries)>0||!!v.metric
      const canExplore=prepared&&!!v.domain&&!!v.purpose&&['preview','ready'].includes(v.phase_code)
      return <Box flexDirection="column" gap={1}>
        <Box flexDirection="column">
          {row('Focus',<Text bold>{v.domain||'Your analytics'}</Text>,<Text dimColor>{liveLabel(s)}</Text>)}
          {row('Goal',<Text>{v.purpose||'Start with what matters to you'}</Text>)}
          {v.now?row('Now',<Text>{nowLine(s, v)}</Text>):''}
        </Box>
        <Box flexDirection="row" gap={2} flexWrap="wrap">
          {rail(v).map((label,i)=><Text key={label} bold={i===step(v)} dimColor={i!==step(v)}>{label}</Text>)}
        </Box>
        {need?<Box flexDirection="row" justifyContent="space-between" alignItems="center" gap={1} flexWrap="wrap" borderStyle="round" paddingX={1}>
          <Text bold>{'Needs you: '+v.needs}</Text>{needButton}</Box>:''}
        {prepared?<Box flexDirection="row" gap={1} flexWrap="wrap">
          {[['metrics','Metrics'],['queries','Queries'],['executed','SQL checks']].map(([key,label])=><Box key={key} flexDirection="column" flexGrow={1} borderStyle="round" borderDimColor paddingX={1} paddingY={1}><Text bold>{count(key)}</Text><Text dimColor>{label}</Text></Box>)}
        </Box>:''}
        <Box flexDirection="column" gap={1} borderStyle="round" borderDimColor padding={1}>
          <Box flexDirection="row" justifyContent="space-between" gap={1} flexWrap="wrap"><Text bold>{v.metric||'What matters to you?'}</Text><Text dimColor>{local?'Saved locally':Number(stats.released)>0?'Shared in Atlan':'In progress'}</Text></Box>
          <Text>{v.metric?meaning:(v.summary||'Choose a business focus in chat. Your first metric will appear here.')}</Text>
          <Text dimColor>{v.check||'Waiting for your business focus'}</Text>
          {v.metric?<Button key="evidence" label={details?'Hide definition and checks':'View definition and checks'} onPress={toggle(()=>{details=!details})}/>:''}
        </Box>
        {details&&v.metric?<Box flexDirection="column" gap={1}><Text bold>{v.metric+' · Evidence'}</Text><Text>{v.meaning}</Text><Text>{'Calculation: '+v.calculation}</Text><Text>{'Includes: '+v.population}</Text><Text>{v.source}</Text><Text>{v.evidence||v.check}</Text>{sql}<Text dimColor>Saved checks are not fresh execution or proof of dashboard parity.</Text></Box>:''}
        {questions.length?<Box flexDirection="column" gap={1}><Text bold>Questions this run can answer</Text>{questions.map(ask)}</Box>:''}
        <Box flexDirection="row" justifyContent="space-between" alignItems="center" gap={1} flexWrap="wrap">
          <Text>{stats.review_required?`${stats.review_required} queries need source review`:canExplore?'Explore your saved definitions':v.next}</Text>
          {stats.review_required||canExplore?<Button key="ask" label={stats.review_required?'Review queries':'Explore a question'} onPress={()=>void draft(stats.review_required?'Show the queries that still need source review and the evidence needed to review them.':'Show a question supported by this analytics run, with its local or released scope.')}/>:''}
        </Box>
        <Box flexDirection="row" flexWrap="wrap" gap={1}>
          <Button key="sources" label={sources?'Hide sources':'View sources'} dimColor onPress={toggle(()=>{sources=!sources})}/>
          {!local?pause('Pause…','Resume…',true):''}
          {!local?keepLocal('Keep local…',true):''}
        </Box>
        {sources?<Box flexDirection="column" gap={1}><Text bold>What we found</Text><Text>{v.summary||v.sources.join(', ')}</Text>{v.findings?.length?findings.map((s:string)=><Text key={'f-'+s} dimColor>{s}</Text>):''}{v.opportunities.map((s:string)=><Text key={s}>{s}</Text>)}</Box>:''}
        {v.warnings.map((s:string)=><Text key={s}>{s}</Text>)}
        {s.stale?<Text>Updates unavailable. Showing the last received evidence.</Text>:''}
        {prepared?<Text dimColor>{`${count('released')} shared in Atlan · ${count('held')} held`}</Text>:''}
        <Text dimColor>{feedback||'Actions prepare a message in chat. Submit it to continue.'}</Text>
      </Box>
    }

    const small=e.surface==='terminal'&&e.props.scroll.bodyRows<18
    // Inline terminal panes may expose only a few rows. Keep the current task
    // visible there; the full evidence is an explicit expansion, not a scroll hunt.
    if(small&&!details){
      const width=Math.max(30,e.props.bodyColumns-2)
      const line=(value:string,room=width)=>value.length>room?value.slice(0,Math.max(1,room-1))+'…':value
      const steps=rail(v).join('  ')
      const showRail=width>=72
      return <Box flexDirection="column" gap={0}>
        {row('Focus',<Text bold>{line(v.domain||'Not chosen yet',width-7-(showRail?steps.length+2:0))}</Text>,showRail?<Text dimColor>{steps}</Text>:undefined)}
        {row('Goal',<Text>{line(v.purpose||'Not chosen yet',width-7)}</Text>)}
        {row('Now',<Text>{line(nowLine(s, v),width-7-liveLabel(s).length-2)}</Text>,<Text dimColor>{liveLabel(s)}</Text>)}
        {v.metric?row('Metric',<Text bold>{line(v.metric+' · '+v.check,width-7)}</Text>):''}
        {need?<Text bold>{line('Needs you: '+v.needs)}</Text>:!v.metric&&v.next&&!v.now?<Text>{line(v.next)}</Text>:''}
        {counted?<Text dimColor>{line(counted)}</Text>:''}
        <Box flexDirection="row" gap={1}>
          {needButton}
          {v.metric?<Button key="evidence" label="View evidence" onPress={toggle(()=>{details=true})}/>:<Button key="evidence" label="View sources" onPress={toggle(()=>{details=true})}/>}
          {!local?pause('Pause','Resume'):''}
          {!local?keepLocal('Keep local'):''}
        </Box>
        <Text dimColor>{line(feedback||(s.stale?'Showing the last received evidence. Continue in chat.':'Buttons draft a chat message. Send it to apply.'))}</Text>
      </Box>
    }

    return <Box flexDirection="column" gap={1}>
      <Box flexDirection="column">
        {row('Focus',<Text bold>{v.domain||'Choose your business focus in chat'}</Text>)}
        {row('Goal',<Text>{v.purpose||'Then choose what you want to accomplish'}</Text>)}
        {row('Now',<Text>{nowLine(s, v)}</Text>)}
      </Box>
      <Text dimColor>{rail(v).join('  ')}</Text>
      {small?<Button key="compact" label="Back to progress" onPress={toggle(()=>{details=false})}/>:''}
      <Text dimColor>{s.stale?'Updates unavailable. Showing the last received evidence.':s.feed?'Following saved progress automatically.':'Saved snapshot; automatic updates are not connected.'}</Text>
      {need?<Text bold>{'Needs you: '+v.needs}</Text>:''}{needButton}
      <Text bold>What we found</Text><Text>{v.summary || v.sources.join(', ')}</Text>
      {v.findings?.length?findings.map((s:string)=><Text key={'f-'+s} dimColor>{s}</Text>):''}
      {v.opportunities.map((s:string)=><Text key={s}>{'• '+s}</Text>)}
      <Text bold>{v.metric||'Your first metric will appear here'}</Text><Text>{v.meaning}</Text><Text>{v.check}</Text>
      {v.metric?<Text>{'Calculation: '+v.calculation}</Text>:''}
      {v.population?<Text>{'Includes: '+v.population}</Text>:''}
      {v.metric?<Button key="evidence" label={details?'Hide evidence':'Inspect source and checks'} onPress={toggle(()=>{details=!details})}/>:''}
      {details?<Text>{v.source+'\nSaved checks are not fresh execution or proof of dashboard parity.'}</Text>:''}
      {details&&v.evidence?<Text>{v.evidence}</Text>:''}
      {details?sql:''}
      {counted?<Text bold>{counted}</Text>:''}{v.warnings.map((s:string)=><Text key={s}>{s}</Text>)}
      {v.usable?<Text bold>Available now</Text>:''}{v.usable?<Text>{v.usable}</Text>:''}
      {v.attention?<Text bold>Needs attention</Text>:''}{v.attention?<Text>{v.attention}</Text>:''}
      {questions.length?<Text bold>Questions this run can answer</Text>:''}{questions.map(ask)}
      {need?'':<Text bold>Up next</Text>}{need?'':<Text>{v.next}</Text>}
      <Button key="details" label="Ask for details" onPress={()=>void draft('Show the selected analytics metric, its SQL, source and checks.')}/>
      {pause('Pause','Resume')}
      {keepLocal('Keep local')}
      <Button key="hide" label="Hide pane" onPress={()=>{s.dismissed=true;s.placed=false;status($, s);void $.ui.close({id:ID})}}/>
      <Text dimColor>Buttons draft a chat message. Send it to apply.</Text>
      {feedback?<Text>{feedback}</Text>:''}
    </Box>
  }).catch(($,e,next)=>next(e))
}
