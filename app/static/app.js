(function(){
  const $ = id => document.getElementById(id);

  const T={
    en:{
      onDevice:"On your device", settings:"Settings", send:"Send", speak:"Speak", addDoc:"Add a document",
      emptyTitle:"Hello. I'm yours.", emptyBody:"Only you and your agent are here. Nothing leaves this machine.",
      chipDoc:"Read a document with me", chipWhat:"What can you do?", placeholder:"Message your agent…",
      listening:"Listening…", listeningBody:"Take your time. I'm hearing you on this device only.", readAloud:"Read aloud",
      reading:"Reading…", readDone:"Read · {n} passages, kept on your disk", readFail:"Couldn't read it",
      noReach:"I can't reach your agent. Make sure Vokter is running on this machine, then try again.",
      serverErr:"Something went wrong.", noReachShort:"Couldn't reach your agent",
      settingsNote:"Everything here stays on your machine. You're in control of all of it.",
      rowDocuments:"Documents", rowEmail:"Email", rowWeb:"Web access", rowTasks:"Scheduled tasks",
      rowVoice:"Voice", rowModel:"Model & tone", rowLanguage:"Language", soon:"Soon",
      noteEmail:"Connect an inbox — indexed on this machine, never uploaded.",
      noteWeb:"Choose which sites your agent may visit.",
      noteTasks:"Let your agent work on a routine and report back.",
      noteVoice:"Speak and listen, fully on-device. Full controls are coming to this screen.",
      noteModel:"Choose how your agent thinks and sounds.",
      docsEmpty:"No documents yet. Attach one from the chat and your agent will read it — all on your disk.",
      passages:"{n} passages", deleted:"Deleted — document and its memory", loading:"Loading…",
      couldntCatch:"Couldn't catch that", micPerm:"Microphone permission needed", voiceUnavail:"Voice isn't available right now",
      langNote:"Choose the language for the app. Your agent still replies in whatever language you write or speak.",
      emailNotConfigured:"Email isn't set up on this machine. Add your inbox settings in the environment to connect it.",
      emailInMemory:"{n} emails indexed on this device", emailNoneSynced:"Connected. No emails synced yet.",
      emailSync:"Sync now", emailSyncing:"Syncing… this can take a while.", emailSynced:"Synced · {n} new, {e} errors",
      emailDelete:"Delete synced emails", emailDeleteConfirm:"Delete all synced emails from your agent's memory?", emailDeleted:"Removed {n} emails",
      webEmpty:"No sites allowed yet. Add one to let your agent visit it.", webAddPh:"https://example.com",
      webAdd:"Add", webAddErr:"Use a full URL, like https://example.com", removeAria:"Remove",
      tasksEmpty:"No scheduled tasks yet.", taskName:"Name", taskGoal:"What should it do?", taskInterval:"Every — e.g. 30m, 2h, 1d",
      taskCreate:"Create task", taskCreateErr:"Couldn't create it. Check the interval (e.g. 30m, 2h, 1d).", taskFill:"Add a name, a goal and an interval.",
      taskPause:"Pause", taskResume:"Resume", taskOn:"on", taskPaused:"paused", taskDeleteConfirm:"Delete \"{name}\"?",
      modelName:"Agent name", modelTone:"Tone", modelMode:"Mode", modelModel:"Chat model", modelModelPh:"e.g. llama3.2:3b",
      modelDefault:"Default (this machine's model)",
      modelModelHint:"Pick a model you've installed. The first reply after switching is slower while it loads.",
      toneFormal:"Formal", toneNeutral:"Neutral", toneFriendly:"Friendly", modeConversational:"Conversational", modeProductive:"Productive",
      modelSave:"Save", modelSaving:"Saving…", modelSaved:"Saved ✓",
      newChat:"New chat", theme:"Theme", themeAria:"Toggle light or dark theme", menu:"Menu", themeLight:"Light", themeDark:"Dark",
      chatsLabel:"Chats", noChats:"No conversations yet"
    },
    es:{
      onDevice:"En tu dispositivo", settings:"Ajustes", send:"Enviar", speak:"Hablar", addDoc:"Añadir un documento",
      emptyTitle:"Hola. Soy tuyo.", emptyBody:"Aquí solo estáis tú y tu agente. Nada sale de esta máquina.",
      chipDoc:"Lee un documento conmigo", chipWhat:"¿Qué puedes hacer?", placeholder:"Escribe a tu agente…",
      listening:"Escuchando…", listeningBody:"Tómate tu tiempo. Te escucho solo en este dispositivo.", readAloud:"Leer en voz alta",
      reading:"Leyendo…", readDone:"Leído · {n} fragmentos, guardado en tu disco", readFail:"No pude leerlo",
      noReach:"No llego a tu agente. Asegúrate de que Vokter está funcionando en esta máquina e inténtalo de nuevo.",
      serverErr:"Algo ha ido mal.", noReachShort:"No llego a tu agente",
      settingsNote:"Todo lo que hay aquí se queda en tu máquina. Tú controlas todo.",
      rowDocuments:"Documentos", rowEmail:"Correo", rowWeb:"Acceso a la web", rowTasks:"Tareas programadas",
      rowVoice:"Voz", rowModel:"Modelo y tono", rowLanguage:"Idioma", soon:"Pronto",
      noteEmail:"Conecta un buzón — se indexa en esta máquina, nunca se sube.",
      noteWeb:"Elige a qué sitios puede acceder tu agente.",
      noteTasks:"Deja que tu agente trabaje con una rutina y te informe.",
      noteVoice:"Habla y escucha, todo en el dispositivo. Los controles completos llegarán a esta pantalla.",
      noteModel:"Elige cómo piensa y suena tu agente.",
      docsEmpty:"Aún no hay documentos. Adjunta uno desde el chat y tu agente lo leerá — todo en tu disco.",
      passages:"{n} fragmentos", deleted:"Borrado — el documento y su memoria", loading:"Cargando…",
      couldntCatch:"No te he entendido", micPerm:"Se necesita permiso del micrófono", voiceUnavail:"La voz no está disponible ahora mismo",
      langNote:"Elige el idioma de la app. Tu agente seguirá respondiendo en el idioma en que escribas o hables.",
      emailNotConfigured:"El correo no está configurado en esta máquina. Añade los datos de tu buzón en el entorno para conectarlo.",
      emailInMemory:"{n} correos indexados en este dispositivo", emailNoneSynced:"Conectado. Aún no hay correos sincronizados.",
      emailSync:"Sincronizar ahora", emailSyncing:"Sincronizando… puede tardar un poco.", emailSynced:"Sincronizado · {n} nuevos, {e} errores",
      emailDelete:"Borrar correos sincronizados", emailDeleteConfirm:"¿Borrar todos los correos sincronizados de la memoria de tu agente?", emailDeleted:"{n} correos eliminados",
      webEmpty:"Aún no hay sitios permitidos. Añade uno para que tu agente pueda visitarlo.", webAddPh:"https://ejemplo.com",
      webAdd:"Añadir", webAddErr:"Usa una URL completa, como https://ejemplo.com", removeAria:"Quitar",
      tasksEmpty:"Aún no hay tareas programadas.", taskName:"Nombre", taskGoal:"¿Qué debe hacer?", taskInterval:"Cada — p. ej. 30m, 2h, 1d",
      taskCreate:"Crear tarea", taskCreateErr:"No se pudo crear. Revisa el intervalo (p. ej. 30m, 2h, 1d).", taskFill:"Añade un nombre, un objetivo y un intervalo.",
      taskPause:"Pausar", taskResume:"Reanudar", taskOn:"activa", taskPaused:"en pausa", taskDeleteConfirm:"¿Borrar \"{name}\"?",
      modelName:"Nombre del agente", modelTone:"Tono", modelMode:"Modo", modelModel:"Modelo de chat", modelModelPh:"p. ej. llama3.2:3b",
      modelDefault:"Por defecto (el modelo de esta máquina)",
      modelModelHint:"Elige un modelo que hayas instalado. La primera respuesta tras cambiar tarda más mientras se carga.",
      toneFormal:"Formal", toneNeutral:"Neutral", toneFriendly:"Cercano", modeConversational:"Conversacional", modeProductive:"Productivo",
      modelSave:"Guardar", modelSaving:"Guardando…", modelSaved:"Guardado ✓",
      newChat:"Nuevo chat", theme:"Tema", themeAria:"Cambiar tema claro u oscuro", menu:"Menú", themeLight:"Claro", themeDark:"Oscuro",
      chatsLabel:"Chats", noChats:"Aún no hay conversaciones"
    }
  };
  const LANGS=[{code:'en',name:'English'},{code:'es',name:'Español'}];
  function loadLang(){ try{return localStorage.getItem('vokter_lang');}catch{return null;} }
  function saveLang(l){ try{localStorage.setItem('vokter_lang',l);}catch{} }
  let lang=(loadLang()||(navigator.language||'en').slice(0,2)); if(!T[lang]) lang='en';
  function t(key,vars){ let s=(T[lang]&&T[lang][key])||T.en[key]||key; if(vars){for(const k in vars)s=s.replace('{'+k+'}',vars[k]);} return s; }
  function applyStatic(){
    document.documentElement.lang=lang;
    document.querySelectorAll('[data-i18n]').forEach(el=>el.textContent=t(el.getAttribute('data-i18n')));
    document.querySelectorAll('[data-i18n-ph]').forEach(el=>el.setAttribute('placeholder',t(el.getAttribute('data-i18n-ph'))));
    document.querySelectorAll('[data-i18n-aria]').forEach(el=>el.setAttribute('aria-label',t(el.getAttribute('data-i18n-aria'))));
  }
  function setLang(l){ lang=l; saveLang(l); applyStatic(); renderHome(); refreshShell(); }

  let tT; function toast(m){const el=$('toast');el.textContent=m;el.classList.add('on');clearTimeout(tT);tT=setTimeout(()=>el.classList.remove('on'),2000);}

  // ── Markdown for the agent's replies. Escape-first + a strict link-scheme allowlist,
  //    so no raw HTML from the model/RAG ever reaches the DOM (only <strong>/<em>/<a>/
  //    <code>/<pre>/lists/quotes we emit ourselves). User messages stay plain textContent.
  function _esc(s){ return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function _safeHref(url){ const u=String(url).trim().replace(/[\u0000-\u0020]/g,''); return /^(https?:|mailto:)/i.test(u)?u:null; }
  function _inline(t){
    const codes=[];
    t=t.replace(/`([^`]+)`/g,(m,c)=>{ codes.push('<code class="md-ic">'+_esc(c)+'</code>'); return '@@C'+(codes.length-1)+'@@'; });
    t=_esc(t);
    t=t.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,(m,txt,url)=>{ const h=_safeHref(url); return h?'<a href="'+_esc(h)+'" target="_blank" rel="noopener noreferrer">'+txt+'</a>':txt; });
    t=t.replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/__([^_]+)__/g,'<strong>$1</strong>');
    t=t.replace(/(^|[^*])\*(?!\s)([^*\n]+?)\*/g,'$1<em>$2</em>').replace(/(^|[^\w_])_(?!\s)([^_\n]+?)_/g,'$1<em>$2</em>');
    return t.replace(/@@C(\d+)@@/g,(m,k)=>codes[+k]);
  }
  function mdToHtml(src){
    src=String(src).replace(/\r\n?/g,'\n');
    const blocks=[];
    src=src.replace(/```([^\n`]*)\n([\s\S]*?)```/g,(m,info,code)=>{
      const lang=(String(info).trim().match(/^[\w+.-]+/)||[''])[0], cls=lang?' class="lang-'+_esc(lang)+'"':'';
      blocks.push('<pre class="md-pre"><code'+cls+'>'+_esc(code.replace(/\n$/,''))+'</code></pre>');
      return '\n@@B'+(blocks.length-1)+'@@\n';
    });
    const lines=src.split('\n'), out=[]; let i=0, para=[];
    const flushP=()=>{ if(para.length){ out.push('<p>'+_inline(para.join('\n')).replace(/\n/g,'<br>')+'</p>'); para=[]; } };
    while(i<lines.length){
      const ln=lines[i], mb=ln.match(/^@@B(\d+)@@$/);
      if(mb){ flushP(); out.push(blocks[+mb[1]]); i++; continue; }
      if(/^\s*$/.test(ln)){ flushP(); i++; continue; }
      const mh=ln.match(/^(#{1,6})\s+(.*)$/);
      if(mh){ flushP(); const n=mh[1].length; out.push('<h'+n+' class="md-h">'+_inline(mh[2].trim())+'</h'+n+'>'); i++; continue; }
      if(/^\s*>\s?/.test(ln)){ flushP(); const qq=[]; while(i<lines.length&&/^\s*>\s?/.test(lines[i])){ qq.push(lines[i].replace(/^\s*>\s?/,'')); i++; } out.push('<blockquote class="md-bq">'+_inline(qq.join('\n')).replace(/\n/g,'<br>')+'</blockquote>'); continue; }
      if(/^\s*[-*+]\s+/.test(ln)){ flushP(); const it=[]; while(i<lines.length&&/^\s*[-*+]\s+/.test(lines[i])){ it.push('<li>'+_inline(lines[i].replace(/^\s*[-*+]\s+/,''))+'</li>'); i++; } out.push('<ul class="md-ul">'+it.join('')+'</ul>'); continue; }
      if(/^\s*\d+\.\s+/.test(ln)){ flushP(); const it=[]; while(i<lines.length&&/^\s*\d+\.\s+/.test(lines[i])){ it.push('<li>'+_inline(lines[i].replace(/^\s*\d+\.\s+/,''))+'</li>'); i++; } out.push('<ol class="md-ol">'+it.join('')+'</ol>'); continue; }
      para.push(ln); i++;
    }
    flushP();
    return out.join('');
  }
  const SHIELD='<svg viewBox="0 0 48 56" fill="none"><path d="M24 2.5 41.5 9.2V26.5C41.5 39.5 33.7 48.7 24 53.5 14.3 48.7 6.5 39.5 6.5 26.5V9.2Z" fill="none" stroke="#2D6A4F" stroke-width="2.6" stroke-linejoin="round"/><circle cx="24" cy="27" r="4.2" fill="#2D6A4F"/></svg>';

  const thread=$('thread'), empty=$('empty'), msgs=$('msgs'), q=$('q');
  let conversationId=null;
  function activate(){ if(empty.style.display!=='none'){empty.style.display='none';msgs.style.display='flex';} }
  function scroll(){ thread.scrollTop=thread.scrollHeight; }
  function addUser(text){ activate(); const d=document.createElement('div');d.className='b me';d.textContent=text;msgs.appendChild(d);scroll();return d; }
  function addFile(name){
    activate();
    const d=document.createElement('div');d.className='b file';
    d.innerHTML='<svg width="16" height="18" viewBox="0 0 24 24" fill="none"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke="#2D6A4F" stroke-width="1.8" stroke-linejoin="round"/><path d="M14 2v6h6" stroke="#2D6A4F" stroke-width="1.8" stroke-linejoin="round"/></svg><div><div class="fn"></div><div class="fs"></div></div>';
    d.querySelector('.fn').textContent=name; d.querySelector('.fs').textContent=t('reading');
    msgs.appendChild(d);scroll();return d;
  }
  // Append the source chips + the read-aloud button to a finished agent bubble.
  // Shared by the one-shot addAgent and the streaming finalize so both look identical.
  function decorateAgent(mc,text,sources){
    if(sources&&sources.length){
      const s=document.createElement('div');s.className='sources';
      sources.slice(0,4).forEach(src=>{const c=document.createElement('span');c.className='src';c.textContent=(typeof src==='string')?src:(src.doc||src.source||'source');s.appendChild(c);});
      mc.appendChild(s);
    }
    const say=document.createElement('button');say.className='say';
    say.innerHTML='<svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M11 5 6 9H3v6h3l5 4V5z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M16 9a3.5 3.5 0 0 1 0 6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg><span>'+t('readAloud')+'</span>';
    say.onclick=()=>speak(text,say);
    mc.appendChild(say);
  }
  function addAgent(text,sources){
    activate();
    const d=document.createElement('div');d.className='b them';
    const av=document.createElement('span');av.className='av';av.innerHTML=SHIELD;
    const mc=document.createElement('div');mc.className='mc';
    const p=document.createElement('div');p.className='md';p.innerHTML=mdToHtml(text);mc.appendChild(p);
    decorateAgent(mc,text,sources);
    d.appendChild(av);d.appendChild(mc);
    msgs.appendChild(d);scroll();return d;
  }
  // A progressively-built agent bubble for streaming: the shield + an empty markdown
  // body appear first, update() re-renders the accumulated text as tokens land, and
  // finalize() paints the authoritative full answer once and adds sources + voice.
  function addAgentStreaming(){
    activate();
    const d=document.createElement('div');d.className='b them';
    const av=document.createElement('span');av.className='av';av.innerHTML=SHIELD;
    const mc=document.createElement('div');mc.className='mc';
    const p=document.createElement('div');p.className='md';mc.appendChild(p);
    d.appendChild(av);d.appendChild(mc);
    msgs.appendChild(d);scroll();
    return {
      update(raw){ p.innerHTML=mdToHtml(raw); scroll(); },
      finalize(text,sources){ p.innerHTML=mdToHtml(text); decorateAgent(mc,text,sources); scroll(); },
    };
  }
  function addThinking(){ activate(); const d=document.createElement('div');d.className='b them thinking';const av=document.createElement('span');av.className='av';av.innerHTML=SHIELD;const mc=document.createElement('div');mc.className='mc';mc.innerHTML='<span class="typing"><i></i><i></i><i></i></span>';d.appendChild(av);d.appendChild(mc);msgs.appendChild(d);scroll();return d; }

  // Route /api/ask through the Electron bridge (window.vokter.ask) when present: main
  // attaches the human-session token so it never lives in page JS — that token is what lets
  // the backend inject personal memory into the reply. Fall back to a direct fetch in a plain
  // browser / Docker (no token → memory withheld, deny-by-default). Returns a Response-like
  // {ok, status, json()} either way, so the handling below is identical for both paths.
  async function askBackend(payload){
    if(window.vokter && window.vokter.ask){
      const {status, body}=await window.vokter.ask(payload);
      return { ok: status>=200 && status<300, status, json: async()=>{ if(body==null) throw new Error('empty body'); return body; } };
    }
    return fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  }
  let streaming=false;
  function setBusy(b){ streaming=b; q.disabled=b; $('sendBtn').disabled=b; }
  async function send(){
    if(streaming) return;                       // in-flight guard: one generation at a time
    const text=q.value.trim(); if(!text) return;
    addUser(text); q.value='';

    // Streaming path — the Electron shell (window.vokter.askStream). Tokens arrive over
    // the onAskToken channel and paint live; the promise resolves with the authoritative
    // full answer + sources for the final render. main.js attaches the human-session
    // token to this request exactly like vokter:ask, so personal memory still injects.
    if(window.vokter && window.vokter.askStream && window.vokter.onAskToken){
      setBusy(true);
      const think=addThinking();
      let view=null, raw='', raf=0;
      const paint=()=>{ if(raf) return; raf=requestAnimationFrame(()=>{ raf=0; if(view) view.update(raw); }); };
      const ensureBubble=()=>{ if(view===null){ think.remove(); view=addAgentStreaming(); } };
      const unsub=window.vokter.onAskToken(d=>{ ensureBubble(); raw+=(d&&d.text)||''; paint(); });
      try{
        const {status,body}=await window.vokter.askStream({question:text,conversation_id:conversationId});
        unsub(); if(raf) cancelAnimationFrame(raf);
        if(status>=200&&status<300&&body){
          conversationId=body.conversation_id; ensureBubble();
          view.finalize(body.answer, body.sources);   // authoritative re-render (fixes any partial markdown)
        }else if(view){ view.finalize(raw||t('serverErr'), []); }
        else{ think.remove(); addAgent(t('serverErr')); }
      }catch{
        unsub(); if(raf) cancelAnimationFrame(raf);
        if(view){ view.finalize(raw||t('noReach'), []); } else { think.remove(); addAgent(t('noReach')); }
      }finally{ setBusy(false); }
      return;
    }

    // Fallback — plain browser / no shell: non-streaming request/response, unchanged.
    const think=addThinking();
    try{
      const r=await askBackend({question:text,conversation_id:conversationId});
      let j; try{j=await r.json();}catch{ think.remove(); addAgent(t('serverErr')); return; }
      think.remove();
      if(r.ok){ conversationId=j.conversation_id; addAgent(j.answer,j.sources); }
      else{ addAgent(j.detail||t('serverErr')); }
    }catch{ think.remove(); addAgent(t('noReach')); }
  }
  $('sendBtn').onclick=send;
  q.addEventListener('keydown',e=>{ if(e.key==='Enter'){e.preventDefault();send();} });

  $('attachBtn').onclick=()=>$('fileInput').click();
  $('chipDoc').onclick=()=>$('fileInput').click();
  $('chipWhat').onclick=()=>{ q.value=t('chipWhat'); send(); };
  $('fileInput').onchange=async e=>{
    const f=e.target.files[0]; if(!f) return;
    const bubble=addFile(f.name); const fs=bubble.querySelector('.fs');
    const fd=new FormData(); fd.append('file',f);
    try{
      const r=await fetch('/api/docs',{method:'POST',body:fd}); const j=await r.json();
      fs.textContent = r.ok ? t('readDone',{n:j.chunks}) : t('readFail')+': '+(j.detail||'error');
      if(r.ok) loadDocCount();
    }catch{ fs.textContent=t('noReachShort'); }
    e.target.value='';
  };

  let rec=null, chunks=[], keep=true;
  function showVoice(on){ $('voiceView').classList.toggle('on',on); }
  async function startVoice(){
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});
      rec=new MediaRecorder(stream); chunks=[]; keep=true;
      rec.ondataavailable=ev=>chunks.push(ev.data);
      rec.onstop=async()=>{
        stream.getTracks().forEach(tr=>tr.stop()); showVoice(false);
        if(!keep) return;
        const blob=new Blob(chunks,{type:'audio/webm'}); const fd=new FormData(); fd.append('audio',blob,'recording.webm');
        try{ const r=await fetch('/api/voice/transcribe',{method:'POST',body:fd}); const j=await r.json();
          if(r.ok&&j.text){ q.value=j.text; q.focus(); } else toast(t('couldntCatch')); }
        catch{ toast(t('noReachShort')); }
      };
      rec.start(); showVoice(true);
    }catch{ toast(t('micPerm')); }
  }
  function stopVoice(save){ keep=save; if(rec&&rec.state==='recording') rec.stop(); else showVoice(false); }
  $('micBtn').onclick=startVoice;
  $('voiceDone').onclick=()=>stopVoice(true);
  $('voiceCancel').onclick=()=>stopVoice(false);
  $('voiceX').onclick=()=>stopVoice(false);
  async function speak(text,btn){
    const label=btn.querySelector('span'); const prev=label?label.textContent:'';
    if(label) label.textContent='…';
    try{
      const r=await fetch('/api/voice/speak',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
      if(r.ok){ const url=URL.createObjectURL(await r.blob()); const a=new Audio(url); a.onended=()=>{URL.revokeObjectURL(url); if(label)label.textContent=prev;}; a.play(); }
      else if(label) label.textContent=prev;
    }catch{ if(label) label.textContent=prev; toast(t('voiceUnavail')); }
  }

  const settingsView=$('settingsView'), slist=$('slist'), sTitle=$('settingsTitle');
  // Was the current sub-view reached via the Settings home (→ Back returns there) or opened
  // directly from the sidebar, e.g. Documents (→ Back closes straight to the chat)?
  let settingsFromHome=true;
  const ICONS={
    documents:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M14 2v6h6" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    email:'<rect x="2" y="4" width="20" height="16" rx="2" stroke="currentColor" stroke-width="1.8"/><path d="m22 7-10 6L2 7" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    web:'<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M3 12h18M12 3a14 14 0 0 1 4 9 14 14 0 0 1-4 9 14 14 0 0 1-4-9 14 14 0 0 1 4-9z" stroke="currentColor" stroke-width="1.8"/>',
    tasks:'<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 7v5l3 2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    voice:'<rect x="9" y="3" width="6" height="12" rx="3" stroke="currentColor" stroke-width="1.8"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    model:'<path d="M12 3a6 6 0 0 1 6 6c0 2.5-1.8 3.5-1.8 6H7.8C7.8 12.5 6 11.5 6 9a6 6 0 0 1 6-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M9 21h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    language:'<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M3 12h18M12 3a14 14 0 0 1 4 9 14 14 0 0 1-4 9 14 14 0 0 1-4-9 14 14 0 0 1 4-9z" stroke="currentColor" stroke-width="1.8"/>'
  };
  const ROWS=[
    {k:'documents',lk:'rowDocuments',type:'docs'},
    {k:'email',lk:'rowEmail',type:'email'},
    {k:'web',lk:'rowWeb',type:'web'},
    {k:'tasks',lk:'rowTasks',type:'tasks'},
    {k:'voice',lk:'rowVoice',type:'placeholder',note:'noteVoice'},
    {k:'model',lk:'rowModel',type:'model'},
    {k:'language',lk:'rowLanguage',type:'lang'}
  ];
  function renderHome(){
    sTitle.textContent=t('settings');
    slist.innerHTML='';
    const note=document.createElement('div');note.className='note';note.textContent=t('settingsNote');slist.appendChild(note);
    ROWS.forEach(row=>{
      const b=document.createElement(row.type==='soon'?'div':'button');
      b.className='srow'+(row.type==='soon'?' soon':'');
      let right;
      if(row.type==='soon') right='<span class="badge">'+t('soon')+'</span>';
      else if(row.k==='documents') right='<span class="rt" id="docCount"></span>';
      else if(row.k==='language') right='<span class="rt">'+((LANGS.find(l=>l.code===lang)||{}).name||'')+'</span>';
      else right='<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="m9 18 6-6-6-6" stroke="#C9C4B8" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      b.innerHTML='<span class="ic"><svg width="18" height="18" viewBox="0 0 24 24" fill="none">'+ICONS[row.k]+'</svg></span><span class="lb">'+t(row.lk)+'</span>'+right;
      if(row.type!=='soon') b.onclick=()=>openRow(row);
      slist.appendChild(b);
    });
    loadDocCount();
  }
  async function loadDocCount(){ try{const r=await fetch('/api/docs');const d=await r.json();const a=$('docCount');if(a)a.textContent=d.length;const b=$('sideDocCount');if(b)b.textContent=d.length;}catch{} }
  function openRow(row){
    settingsFromHome=true;                 // rows are only reached from the Settings home
    if(row.type==='docs') return renderDocs();
    if(row.type==='lang') return renderLang();
    if(row.type==='email') return renderEmail();
    if(row.type==='web') return renderWeb();
    if(row.type==='tasks') return renderTasks();
    if(row.type==='model') return renderModel();
    sTitle.textContent=t(row.lk);
    slist.innerHTML='<div class="subwrap"><div class="placeholder">'+t(row.note)+'</div></div>';
  }
  function renderLang(){
    sTitle.textContent=t('rowLanguage');
    slist.innerHTML='<div class="subwrap"><div class="placeholder">'+t('langNote')+'</div></div>';
    const wrap=slist.querySelector('.subwrap');
    LANGS.forEach(l=>{
      const b=document.createElement('button');b.className='langrow';
      b.innerHTML='<span>'+l.name+'</span>'+(l.code===lang?'<span class="tick"><svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M5 13l4 4L19 7" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg></span>':'');
      b.onclick=()=>{ setLang(l.code); };
      wrap.appendChild(b);
    });
  }
  async function renderDocs(){
    sTitle.textContent=t('rowDocuments');
    slist.innerHTML='<div class="subwrap" id="docWrap"><div class="placeholder">'+t('loading')+'</div></div>';
    const wrap=$('docWrap');
    try{
      const r=await fetch('/api/docs'); const docs=await r.json();
      if(!docs.length){ wrap.innerHTML='<div class="placeholder">'+t('docsEmpty')+'</div>'; return; }
      wrap.innerHTML='';
      docs.forEach(d=>{
        const row=document.createElement('div');row.className='drow';
        row.innerHTML='<span class="dn"></span><span class="dc">'+t('passages',{n:d.chunks})+'</span><button class="del" aria-label="Delete">&times;</button>';
        row.querySelector('.dn').textContent=d.doc;
        row.querySelector('.del').onclick=async()=>{ await fetch('/api/docs/'+encodeURIComponent(d.doc),{method:'DELETE'}); renderDocs(); toast(t('deleted')); };
        wrap.appendChild(row);
      });
    }catch{ wrap.innerHTML='<div class="placeholder">'+t('noReachShort')+'</div>'; }
  }

  // These four panels talk to admin endpoints that are NOT gated on the human-session
  // token (only /api/ask and /api/memory/suggest are), so they use plain fetch — same as
  // the old wired UI did. All user-controlled values are written via textContent, never
  // interpolated into innerHTML: the shell CSP only exists in Electron, so this is the real
  // XSS guard for the browser/Docker path.
  function _fmtInterval(sec){
    const m=Math.floor(sec/60);
    if(m<60) return m+'m';
    const h=Math.floor(m/60);
    return h<24 ? h+'h' : Math.floor(h/24)+'d';
  }
  function _sub(){ slist.innerHTML=''; const w=document.createElement('div'); w.className='subwrap'; slist.appendChild(w); return w; }
  function _note(wrap,key){ const n=document.createElement('div'); n.className='note'; n.textContent=t(key); wrap.appendChild(n); return n; }
  function _placeholder(text){ const p=document.createElement('div'); p.className='placeholder'; p.textContent=text; return p; }

  // ── Email: status / sync / delete. IMAP credentials are backend-only (env), never sent
  //    to the page, so there is no password field to expose — same surface as the old UI.
  async function renderEmail(){
    sTitle.textContent=t('rowEmail');
    const wrap=_sub(); _note(wrap,'noteEmail');
    const status=document.createElement('div'); status.className='smeta'; status.textContent=t('loading'); wrap.appendChild(status);
    const acts=document.createElement('div'); acts.className='sactions'; wrap.appendChild(acts);
    const syncBtn=document.createElement('button'); syncBtn.className='sbtn'; syncBtn.textContent=t('emailSync');
    const delBtn=document.createElement('button'); delBtn.className='sbtn ghost'; delBtn.textContent=t('emailDelete');
    acts.appendChild(syncBtn); acts.appendChild(delBtn);
    async function refresh(){
      try{
        const j=await (await fetch('/api/email/status')).json();
        if(!j.configured){ status.textContent=t('emailNotConfigured'); syncBtn.style.display='none'; delBtn.style.display='none'; return; }
        syncBtn.style.display='';
        status.textContent = j.synced_emails>0 ? t('emailInMemory',{n:j.synced_emails}) : t('emailNoneSynced');
        delBtn.style.display = j.synced_emails>0 ? '' : 'none';
      }catch{ status.textContent=t('noReachShort'); }
    }
    syncBtn.onclick=async()=>{
      syncBtn.disabled=true; status.textContent=t('emailSyncing');
      try{ const r=await fetch('/api/email/sync',{method:'POST'}); const j=await r.json();
        if(r.ok){ toast(t('emailSynced',{n:j.synced,e:j.errors})); await refresh(); }
        else{ status.textContent=(typeof j.detail==='string')?j.detail:t('serverErr'); }
      }catch{ status.textContent=t('noReachShort'); }
      syncBtn.disabled=false;
    };
    delBtn.onclick=async()=>{
      if(!confirm(t('emailDeleteConfirm'))) return;
      try{ const j=await (await fetch('/api/email/all',{method:'DELETE'})).json();
        toast(t('emailDeleted',{n:j.emails_removed})); await refresh();
      }catch{ toast(t('noReachShort')); }
    };
    refresh();
  }

  // ── Web access: the browsing allow-list (list / add / remove).
  async function renderWeb(){
    sTitle.textContent=t('rowWeb');
    const wrap=_sub(); _note(wrap,'noteWeb');
    const addrow=document.createElement('div'); addrow.className='saddrow';
    const input=document.createElement('input'); input.type='text'; input.className='sin'; input.placeholder=t('webAddPh'); input.setAttribute('autocomplete','off');
    const addBtn=document.createElement('button'); addBtn.className='sbtn'; addBtn.textContent=t('webAdd');
    addrow.appendChild(input); addrow.appendChild(addBtn); wrap.appendChild(addrow);
    const list=document.createElement('div'); wrap.appendChild(list);
    async function refresh(){
      list.innerHTML='';
      try{
        const perms=await (await fetch('/api/browse/permissions')).json();
        if(!perms.length){ list.appendChild(_placeholder(t('webEmpty'))); return; }
        perms.forEach(perm=>{
          const row=document.createElement('div'); row.className='drow';
          const dn=document.createElement('span'); dn.className='dn'; dn.textContent=perm.pattern;
          const del=document.createElement('button'); del.className='del'; del.setAttribute('aria-label',t('removeAria')); del.textContent='×';
          del.onclick=async()=>{ try{ await fetch('/api/browse/permissions/'+encodeURIComponent(perm.pattern),{method:'DELETE'}); }catch{} refresh(); };
          row.appendChild(dn); row.appendChild(del); list.appendChild(row);
        });
      }catch{ list.appendChild(_placeholder(t('noReachShort'))); }
    }
    async function add(){
      const pattern=input.value.trim(); if(!pattern) return;
      addBtn.disabled=true;
      try{ const r=await fetch('/api/browse/permissions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({pattern})});
        if(r.ok){ input.value=''; refresh(); } else{ toast(t('webAddErr')); }
      }catch{ toast(t('noReachShort')); }
      addBtn.disabled=false;
    }
    addBtn.onclick=add;
    input.addEventListener('keydown',e=>{ if(e.key==='Enter'){e.preventDefault();add();} });
    refresh();
  }

  // ── Scheduled tasks: list / create / pause-resume / delete (pause-resume = the old UI's edit).
  async function renderTasks(){
    sTitle.textContent=t('rowTasks');
    const wrap=_sub(); _note(wrap,'noteTasks');
    const nameI=document.createElement('input'); nameI.type='text'; nameI.className='sin'; nameI.placeholder=t('taskName'); nameI.setAttribute('autocomplete','off');
    const goalI=document.createElement('input'); goalI.type='text'; goalI.className='sin'; goalI.placeholder=t('taskGoal'); goalI.setAttribute('autocomplete','off');
    const intI=document.createElement('input'); intI.type='text'; intI.className='sin'; intI.placeholder=t('taskInterval'); intI.setAttribute('autocomplete','off');
    [nameI,goalI,intI].forEach(el=>{ const f=document.createElement('div'); f.className='sfield'; f.appendChild(el); wrap.appendChild(f); });
    const createBtn=document.createElement('button'); createBtn.className='sbtn'; createBtn.textContent=t('taskCreate');
    const acts=document.createElement('div'); acts.className='sactions'; acts.appendChild(createBtn); wrap.appendChild(acts);
    const list=document.createElement('div'); list.style.marginTop='6px'; wrap.appendChild(list);
    async function refresh(){
      list.innerHTML='';
      try{
        const tasks=await (await fetch('/api/schedule')).json();
        if(!tasks.length){ list.appendChild(_placeholder(t('tasksEmpty'))); return; }
        tasks.forEach(task=>{
          const row=document.createElement('div'); row.className='drow';
          const dn=document.createElement('span'); dn.className='dn'; dn.textContent=task.name;
          const dc=document.createElement('span'); dc.className='dc'+(task.enabled?' on':''); dc.textContent=_fmtInterval(task.interval_seconds)+' · '+(task.enabled?t('taskOn'):t('taskPaused'));
          const acts2=document.createElement('span'); acts2.className='acts';
          const tg=document.createElement('button'); tg.className='sbtn mini ghost'; tg.textContent=task.enabled?t('taskPause'):t('taskResume');
          tg.onclick=async()=>{ tg.disabled=true; try{ await fetch('/api/schedule/'+task.id,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!task.enabled})}); }catch{} refresh(); };
          const del=document.createElement('button'); del.className='del'; del.setAttribute('aria-label',t('removeAria')); del.textContent='×';
          del.onclick=async()=>{ if(!confirm(t('taskDeleteConfirm',{name:task.name}))) return; try{ await fetch('/api/schedule/'+task.id,{method:'DELETE'}); }catch{} refresh(); };
          acts2.appendChild(tg); acts2.appendChild(del);
          row.appendChild(dn); row.appendChild(dc); row.appendChild(acts2); list.appendChild(row);
        });
      }catch{ list.appendChild(_placeholder(t('noReachShort'))); }
    }
    createBtn.onclick=async()=>{
      const name=nameI.value.trim(), goal=goalI.value.trim(), interval=intI.value.trim();
      if(!name||!goal||!interval){ toast(t('taskFill')); return; }
      createBtn.disabled=true;
      try{ const r=await fetch('/api/schedule',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name,goal,interval})});
        if(r.ok){ nameI.value='';goalI.value='';intI.value=''; refresh(); } else{ toast(t('taskCreateErr')); }
      }catch{ toast(t('noReachShort')); }
      createBtn.disabled=false;
    };
    refresh();
  }

  // ── Model & tone: agent name / tone / mode / chat model, from and to /api/config.
  async function renderModel(){
    sTitle.textContent=t('rowModel');
    const wrap=_sub(); _note(wrap,'noteModel');
    function field(labelKey,control){ const f=document.createElement('div'); f.className='sfield'; const l=document.createElement('label'); l.textContent=t(labelKey); f.appendChild(l); f.appendChild(control); wrap.appendChild(f); }
    const nameI=document.createElement('input'); nameI.type='text'; nameI.className='sin'; nameI.setAttribute('autocomplete','off'); field('modelName',nameI);
    const toneS=document.createElement('select'); toneS.className='sin';
    [['formal','toneFormal'],['neutral','toneNeutral'],['friendly','toneFriendly']].forEach(([v,k])=>{ const o=document.createElement('option'); o.value=v; o.textContent=t(k); toneS.appendChild(o); });
    field('modelTone',toneS);
    const modeS=document.createElement('select'); modeS.className='sin';
    [['conversational','modeConversational'],['productive','modeProductive']].forEach(([v,k])=>{ const o=document.createElement('option'); o.value=v; o.textContent=t(k); modeS.appendChild(o); });
    field('modelMode',modeS);
    // Chat model: a dropdown of the models installed in the local engine (GET /api/models),
    // plus a "Default" entry (blank → this machine's default model). Populated after we know
    // the saved value, so the current model is always selectable even if it's no longer listed.
    const modelS=document.createElement('select'); modelS.className='sin'; field('modelModel',modelS);
    const hint=document.createElement('div'); hint.className='smeta'; hint.textContent=t('modelModelHint'); wrap.appendChild(hint);
    const saveBtn=document.createElement('button'); saveBtn.className='sbtn'; saveBtn.textContent=t('modelSave');
    const status=document.createElement('span'); status.className='smeta';
    const acts=document.createElement('div'); acts.className='sactions'; acts.appendChild(saveBtn); acts.appendChild(status); wrap.appendChild(acts);
    let saved='';
    try{ const cfg=await (await fetch('/api/config')).json();
      nameI.value=cfg.agent_name||'Vokter'; toneS.value=cfg.tone||'neutral'; modeS.value=cfg.mode||'conversational'; saved=cfg.chat_model||'';
    }catch{ status.textContent=t('noReachShort'); }
    // Build the model options: Default first, then installed models; ensure the saved one is present.
    let models=[]; try{ models=(await (await fetch('/api/models')).json()).models||[]; }catch{}
    const opts=['']; if(saved && !models.includes(saved)) opts.push(saved); models.forEach(m=>{ if(!opts.includes(m)) opts.push(m); });
    opts.forEach(m=>{ const o=document.createElement('option'); o.value=m; o.textContent=m||t('modelDefault'); modelS.appendChild(o); });
    modelS.value=saved;
    saveBtn.onclick=async()=>{
      saveBtn.disabled=true; status.textContent=t('modelSaving');
      try{ const r=await fetch('/api/config',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({
        agent_name:nameI.value.trim()||null, tone:toneS.value, mode:modeS.value, chat_model:modelS.value
      })});
        const d=await r.json();
        if(r.ok){ status.textContent=t('modelSaved'); setTimeout(()=>{status.textContent='';},2000); }
        else{ status.textContent=(typeof d.detail==='string')?d.detail:t('serverErr'); }
      }catch{ status.textContent=t('noReachShort'); }
      saveBtn.disabled=false;
    };
  }

  // ── Shell controls: sidebar drawer, new chat, settings/docs, language, theme ──
  const appEl=$('app');
  function closeSidebar(){ appEl.classList.remove('side-open'); }
  $('navToggle').onclick=()=>appEl.classList.toggle('side-open');
  $('sideBackdrop').onclick=closeSidebar;

  function openSettings(){ renderHome(); settingsView.classList.add('on'); closeSidebar(); }
  $('sideSettings').onclick=openSettings;
  $('sideDocs').onclick=()=>{ settingsFromHome=false; settingsView.classList.add('on'); renderDocs(); closeSidebar(); };
  // Back: from a sub-view opened via the Settings home → return to the home list; otherwise
  // (Settings home itself, or a sub-view opened straight from the sidebar) → close to the chat.
  $('settingsBack').onclick=()=>{
    if(sTitle.textContent!==t('settings') && settingsFromHome){ renderHome(); }
    else { settingsView.classList.remove('on'); }
  };

  $('newChat').onclick=()=>{ conversationId=null; msgs.innerHTML=''; msgs.style.display='none'; empty.style.display=''; closeSidebar(); q.focus(); };

  // Language toggle across the two languages — routes through setLang (updates the whole UI).
  $('langBtn').onclick=()=>{ const other=LANGS.find(l=>l.code!==lang); if(other) setLang(other.code); };

  // Light/dark theme, persisted. refreshShell() keeps the sidebar's dynamic labels in sync
  // (setLang can't know about them), so it's called from both applyTheme() and setLang().
  const SUN='<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.8"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
  const MOON='<svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>';
  function loadTheme(){ try{ return localStorage.getItem('vokter_theme'); }catch{ return null; } }
  let theme = loadTheme()==='dark' ? 'dark' : 'light';
  function refreshShell(){
    const ln=$('langName'); if(ln) ln.textContent=(LANGS.find(l=>l.code===lang)||{}).name||lang;
    const ti=$('themeIcon'); if(ti) ti.innerHTML = theme==='dark' ? MOON : SUN;
    const tn=$('themeName'); if(tn) tn.textContent = t(theme==='dark'?'themeDark':'themeLight');
  }
  function applyTheme(){ document.documentElement.setAttribute('data-theme', theme); refreshShell(); }
  $('themeBtn').onclick=()=>{ theme = theme==='dark'?'light':'dark'; try{ localStorage.setItem('vokter_theme',theme); }catch{} applyTheme(); };

  applyStatic();
  applyTheme();
  loadDocCount();
  q.focus();
})();
