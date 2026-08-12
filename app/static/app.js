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
      rowVoice:"Voice", rowModel:"Model & tone", rowLanguage:"Language", rowPay:"Payments", soon:"Soon",
      noteEmail:"Connect an inbox — indexed on this machine, never uploaded. Full controls are coming to this screen.",
      noteWeb:"Choose which sites your agent may visit. Full controls are coming to this screen.",
      noteTasks:"Let your agent work on a routine and report back. Full controls are coming to this screen.",
      noteVoice:"Speak and listen, fully on-device. Full controls are coming to this screen.",
      noteModel:"Choose how your agent thinks and sounds. Full controls are coming to this screen.",
      docsEmpty:"No documents yet. Attach one from the chat and your agent will read it — all on your disk.",
      passages:"{n} passages", deleted:"Deleted — document and its memory", loading:"Loading…",
      couldntCatch:"Couldn't catch that", micPerm:"Microphone permission needed", voiceUnavail:"Voice isn't available right now",
      langNote:"Choose the language for the app. Your agent still replies in whatever language you write or speak."
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
      rowVoice:"Voz", rowModel:"Modelo y tono", rowLanguage:"Idioma", rowPay:"Pagos", soon:"Pronto",
      noteEmail:"Conecta un buzón — se indexa en esta máquina, nunca se sube. Los controles completos llegarán a esta pantalla.",
      noteWeb:"Elige a qué sitios puede acceder tu agente. Los controles completos llegarán a esta pantalla.",
      noteTasks:"Deja que tu agente trabaje con una rutina y te informe. Los controles completos llegarán a esta pantalla.",
      noteVoice:"Habla y escucha, todo en el dispositivo. Los controles completos llegarán a esta pantalla.",
      noteModel:"Elige cómo piensa y suena tu agente. Los controles completos llegarán a esta pantalla.",
      docsEmpty:"Aún no hay documentos. Adjunta uno desde el chat y tu agente lo leerá — todo en tu disco.",
      passages:"{n} fragmentos", deleted:"Borrado — el documento y su memoria", loading:"Cargando…",
      couldntCatch:"No te he entendido", micPerm:"Se necesita permiso del micrófono", voiceUnavail:"La voz no está disponible ahora mismo",
      langNote:"Elige el idioma de la app. Tu agente seguirá respondiendo en el idioma en que escribas o hables."
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
  function setLang(l){ lang=l; saveLang(l); applyStatic(); renderHome(); }

  let tT; function toast(m){const el=$('toast');el.textContent=m;el.classList.add('on');clearTimeout(tT);tT=setTimeout(()=>el.classList.remove('on'),2000);}

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
  function addAgent(text,sources){
    activate();
    const d=document.createElement('div');d.className='b them';
    const p=document.createElement('div');p.textContent=text;d.appendChild(p);
    if(sources&&sources.length){
      const s=document.createElement('div');s.className='sources';
      sources.slice(0,4).forEach(src=>{const c=document.createElement('span');c.className='src';c.textContent=(typeof src==='string')?src:(src.doc||src.source||'source');s.appendChild(c);});
      d.appendChild(s);
    }
    const say=document.createElement('button');say.className='say';
    say.innerHTML='<svg width="13" height="13" viewBox="0 0 24 24" fill="none"><path d="M11 5 6 9H3v6h3l5 4V5z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M16 9a3.5 3.5 0 0 1 0 6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg><span>'+t('readAloud')+'</span>';
    say.onclick=()=>speak(text,say);
    d.appendChild(say);
    msgs.appendChild(d);scroll();return d;
  }
  function addThinking(){ activate(); const d=document.createElement('div');d.className='b them';d.innerHTML='<span class="typing"><i></i><i></i><i></i></span>';msgs.appendChild(d);scroll();return d; }

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
  async function send(){
    const text=q.value.trim(); if(!text) return;
    addUser(text); q.value='';
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
  const ICONS={
    documents:'<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M14 2v6h6" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    email:'<rect x="2" y="4" width="20" height="16" rx="2" stroke="currentColor" stroke-width="1.8"/><path d="m22 7-10 6L2 7" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/>',
    web:'<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M3 12h18M12 3a14 14 0 0 1 4 9 14 14 0 0 1-4 9 14 14 0 0 1-4-9 14 14 0 0 1 4-9z" stroke="currentColor" stroke-width="1.8"/>',
    tasks:'<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M12 7v5l3 2" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    voice:'<rect x="9" y="3" width="6" height="12" rx="3" stroke="currentColor" stroke-width="1.8"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    model:'<path d="M12 3a6 6 0 0 1 6 6c0 2.5-1.8 3.5-1.8 6H7.8C7.8 12.5 6 11.5 6 9a6 6 0 0 1 6-6z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M9 21h6" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>',
    language:'<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="1.8"/><path d="M3 12h18M12 3a14 14 0 0 1 4 9 14 14 0 0 1-4 9 14 14 0 0 1-4-9 14 14 0 0 1 4-9z" stroke="currentColor" stroke-width="1.8"/>',
    pay:'<rect x="2" y="6" width="20" height="13" rx="2" stroke="currentColor" stroke-width="1.8"/><path d="M2 10h20M16 15h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/>'
  };
  const ROWS=[
    {k:'documents',lk:'rowDocuments',type:'docs'},
    {k:'email',lk:'rowEmail',type:'placeholder',note:'noteEmail'},
    {k:'web',lk:'rowWeb',type:'placeholder',note:'noteWeb'},
    {k:'tasks',lk:'rowTasks',type:'placeholder',note:'noteTasks'},
    {k:'voice',lk:'rowVoice',type:'placeholder',note:'noteVoice'},
    {k:'model',lk:'rowModel',type:'placeholder',note:'noteModel'},
    {k:'language',lk:'rowLanguage',type:'lang'},
    {k:'pay',lk:'rowPay',type:'soon'}
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
  async function loadDocCount(){ try{const r=await fetch('/api/docs');const d=await r.json();const el=$('docCount');if(el)el.textContent=d.length;}catch{} }
  function openRow(row){
    if(row.type==='docs') return renderDocs();
    if(row.type==='lang') return renderLang();
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

  $('openSettings').onclick=()=>{ renderHome(); settingsView.classList.add('on'); };
  $('settingsBack').onclick=()=>{ if(sTitle.textContent!==t('settings')){ renderHome(); } else { settingsView.classList.remove('on'); } };

  applyStatic();
  q.focus();
})();
