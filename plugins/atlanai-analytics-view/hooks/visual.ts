// Presentation only. All supplied strings are escaped before entering SVG text.
const esc = (v: unknown) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[c]!))
const short = (v: string, n: number) => v.length > n ? v.slice(0, n - 1).trimEnd() + '…' : v
const lines = (v: string, n: number, count: number) => {
  const words = v.split(/\s+/), result: string[] = []
  let line = ''
  for (const word of words) {
    if (line && (line + ' ' + word).length > n) { result.push(line); line = word }
    else line += (line ? ' ' : '') + word
  }
  if (line) result.push(line)
  return result.slice(0, count).map((s, i) => i === count - 1 && result.length > count ? short(s, n - 1) + '…' : short(s, n))
}

export function visualBoard(v: any, stale: boolean, live: boolean): {source: string; alt: string} {
  const stats = v.stats || {}
  const num = (key: string) => typeof stats[key] === 'number' ? String(stats[key]) : '—'
  const local = v.interaction_mode === 'local'
  const review = stats.review_required || 0
  const active = ['preview','publish','ready'].includes(v.phase_code) ? 2 : v.metric ? 1 : 0
  const text = (x:number,y:number,value:unknown,size=15,cls='body',weight=400) =>
    `<text x="${x}" y="${y}" class="${cls}" font-size="${size}" font-weight="${weight}">${esc(value)}</text>`
  const wrapped = (x:number,y:number,value:string,n=58,count=2) => lines(value,n,count).map((s,i)=>text(x,y+i*22,s)).join('')
  const steps = ['Discover','Prepare','Use'].map((name,i)=>{
    const x=26+i*166,done=i<active,current=i===active
    return `<circle cx="${x+11}" cy="112" r="11" class="${done||current?'accentFill':'lineFill'}"/>`
      +text(x+7,116,done?'✓':i+1,12,done||current?'onAccent':'muted',600)
      +text(x+31,117,name,15,current?'accent':'muted',current?600:400)
      +(i<2?`<path d="M${x+116} 112h28" class="stroke"/>`:'')
  }).join('')
  const cards = [['metrics','Metrics'],['queries','Saved queries'],['executed','SQL checks']].map(([key,label],i)=>{
    const x=16+i*168
    return `<rect x="${x}" y="146" width="156" height="86" rx="12" class="card"/>`
      +text(x+17,185,num(key),30,'strong',600)+text(x+17,212,label,13,'muted')
  }).join('')
  const heroTitle=v.metric||'What matters to you?'
  const heroMeaning=v.metric?v.meaning:'Choose a business focus in chat. Your first metric and its checks will appear here.'
  const headlineMeaning=heroMeaning.match(/^.*?[.!?](?:\s|$)/)?.[0].trim()||heroMeaning
  const note=review?`${review} queries still need source review`:v.metric?'Dashboard value parity is unmeasured':'No definitions prepared yet'
  const badge=local?'LOCAL ONLY':Number(stats.released)>0?'RELEASED SCOPE':'IN PROGRESS'
  const result=`<svg xmlns="http://www.w3.org/2000/svg" width="520" height="538" viewBox="0 0 520 538">
    <style>
      text{font-family:system-ui,-apple-system,sans-serif}.bg{fill:#171b19}.card{fill:#222a26}.hero{fill:#1d3028;stroke:#3f6654}.strong,.body{fill:#eff5f1}.muted{fill:#acbbb2}.accent{fill:#93e0b9}.accentFill{fill:#93e0b9}.onAccent{fill:#173324}.lineFill{fill:#344139}.stroke{stroke:#48574e;stroke-width:1.5}.warning{fill:#f2cb82}.pill{fill:#34443a}
      @media(prefers-color-scheme:light){.bg{fill:#f5f7f5}.card{fill:#e9efeb}.hero{fill:#edf7f0;stroke:#b0cbbb}.strong,.body{fill:#183127}.muted{fill:#52675b}.accent{fill:#1c7147}.accentFill{fill:#237b50}.onAccent{fill:#fff}.lineFill{fill:#dde5df}.stroke{stroke:#bdccc1}.warning{fill:#875d12}.pill{fill:#dbeadf}}
    </style>
    <rect width="520" height="538" rx="16" class="bg"/>
    ${text(24,29,'YOUR ANALYTICS',11,'muted',600)}
    <circle cx="414" cy="25" r="3" class="${stale?'lineFill':'accentFill'}"/>
    ${text(424,29,stale?'OFFLINE':live?'LIVE':'SNAPSHOT',10,'muted',600)}
    ${text(24,66,short(v.domain||'A place to start',29),29,'strong',600)}
    ${text(24,88,short(v.purpose||'From your sources to a useful first question',62),13,'muted')}
    ${steps}${cards}
    <rect x="16" y="248" width="488" height="220" rx="14" class="hero"/>
    ${text(36,277,v.metric?'SELECTED METRIC':'YOUR NEXT STEP',11,'accent',600)}
    ${text(36,313,short(heroTitle,29),28,'strong',600)}
    ${wrapped(36,342,headlineMeaning,57,3)}
    <path d="M36 405h448" class="stroke"/>
    ${text(36,434,short(v.check||'Waiting for your business focus',62),14,'accent',600)}
    ${text(36,455,'Calculation, source and SQL are available in Evidence',11,'muted')}
    <rect x="16" y="484" width="112" height="28" rx="14" class="pill"/>
    ${text(28,503,badge,10,'accent',600)}
    ${text(142,503,short(note,49),12,review?'warning':'muted')}
    ${text(24,528,`${num('released')} shared in Atlan · ${num('held')} held`,11,'muted')}
  </svg>`
  return {source:result,alt:[v.domain,v.purpose,v.phase,v.counts,heroTitle,heroMeaning,v.check,note,local?'Kept local':'',stale?'Updates unavailable':''].filter(Boolean).join('. ')}
}
