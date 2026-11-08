'use strict';
const $ = (id) => document.getElementById(id);
const state = { user: null, csrf: null, subjects: [], posts: [], section: 'explore', query: '', subject: '', category: '', deadline: '' };
const categories = { project: 'Project', group: 'Group work', midterm: 'Midterm', endterm: 'Endterm', homework: 'Homework', study: 'Study session' };
const formats = { online: 'Online', offline: 'In person', either: 'Any format' };
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const initials = (a,b) => (String(a||'')[0] || '') + (String(b||'')[0] || '');
const avatarVersion = {};
const avatar=(id,first,last,has)=> has?`<img class="user-avatar" src="/api/avatar/${Number(id)}?v=${avatarVersion[id]||0}" alt="${esc(initials(first,last))}" onerror="this.style.display='none';this.insertAdjacentHTML('afterend','<span class=profile-initials>${esc(initials(first,last))}</span>')">`:`<span class="profile-initials">${esc(initials(first,last))}</span>`;
const dateText = (x) => { try { return new Date(x.replace(' ','T')+'Z').toLocaleDateString('en-US',{day:'numeric',month:'short'}); } catch {return '';} };
async function api(url, method='GET', body) {
 const opts = {method,headers:{'Accept':'application/json'}};
 if (body !== undefined) {opts.headers['Content-Type']='application/json';opts.body=JSON.stringify(body);}
 if (method !== 'GET' && state.csrf) opts.headers['X-CSRF-Token']=state.csrf;
 let res;
 try { res = await fetch(url,opts); } catch {throw new Error('Server unavailable. Make sure the application is running.');}
 const data = await res.json();
 if (!res.ok) throw new Error(data.error || 'An error occurred');
 return data;
}
function toast(msg,error=false){const el=document.createElement('div');el.className='toast'+(error?' error':'');el.textContent=msg;$('toast-area').append(el);setTimeout(()=>el.remove(),4600);}
// Optional notification sounds. Browser audio is activated only by the user's click.
let soundEnabled = false;
let audioCtx = null;
const notificationSeen = {userId:null, received:null, sent:null};
function playNotice(kind='incoming') {
 if (!soundEnabled) return;
 try {
  audioCtx ||= new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === 'suspended') audioCtx.resume().catch(()=>{});
  const notes=kind==='sent'?[587.33,783.99]:kind==='accepted'?[659.25,880,1046.5]:[659.25,783.99];
  notes.forEach((freq,index)=>{
   const t=audioCtx.currentTime + index*.11;
   const osc=audioCtx.createOscillator(),gain=audioCtx.createGain();
   osc.type='sine';osc.frequency.value=freq;
   gain.gain.setValueAtTime(.0001,t);
   gain.gain.exponentialRampToValueAtTime(.09,t+.018);
   gain.gain.exponentialRampToValueAtTime(.0001,t+.16);
   osc.connect(gain);gain.connect(audioCtx.destination);
   osc.start(t);osc.stop(t+.18);
  });
 }catch(e){ console.warn('Notification audio unavailable:',e); }
}
function setSoundEnabled(value) {
 soundEnabled=!!value;
 document.getElementById('notification-sounds').checked=soundEnabled;
 if (soundEnabled) playNotice('sent');
}
async function checkNotifications() {
 if (!state.user || document.hidden) return;
 try {
  const [received,sent]=await Promise.all([
   api('/api/applications?scope=received'),api('/api/applications?scope=sent')
  ]);
  const latestReceived=received.applications.map(a=>a.id).sort((a,b)=>b-a);
  const latestSent=Object.fromEntries(sent.applications.map(a=>[a.id,a.status]));
  if(notificationSeen.userId!==state.user.id){
    notificationSeen.userId=state.user.id;
    notificationSeen.received=new Set(latestReceived);
    notificationSeen.sent=latestSent;
    return;
  }
  const newCount=latestReceived.filter(id=>!notificationSeen.received.has(id)).length;
  if (newCount) {
    playNotice('incoming');
    toast(`${newCount} new application${newCount===1?'':'s'} received!`);
    if(state.section==='received') await loadApplications('received');
  }
  for (const [id,status] of Object.entries(latestSent)) {
    const previous=notificationSeen.sent[id];
    if(previous==='pending' && (status==='accepted'||status==='declined')) {
      playNotice(status==='accepted'?'accepted':'incoming');
      toast(status==='accepted'?'Your application was accepted!':'Your application was declined.');
      if(state.section==='sent')await loadApplications('sent');
    }
  }
  notificationSeen.received=new Set(latestReceived);
  notificationSeen.sent=latestSent;
 }catch(e){ /* Temporary network failures must not interrupt the page. */ }
}
function showError(form,error){let area=form.querySelector('.inline-error');if(!area){area=document.createElement('div');area.className='inline-error';const btn=form.querySelector('[type=submit]');form.insertBefore(area,btn);}area.textContent=error.message||error;}
function modal(content,wide=false){$('modal-content').innerHTML=content;document.querySelector('.modal').classList.toggle('wide',wide);$('modal-overlay').hidden=false;const first=$('modal-content').querySelector('input,select,textarea');if(first)first.focus();}

let chatPoll=null;
let currentChatPost=null;
let chatNotificationSnapshot=null;
let chatNotificationLoading=false;
let chatConversations=[];
function renderChats(){
 const area=$('content-list');
 area.innerHTML=`<div class="chats-heading"><div><h2>Chats</h2><p>Conversations with your accepted study groups</p></div><span>${chatConversations.length} conversations</span></div>`+
 (chatConversations.length?`<div class="chats-list">${chatConversations.map(c=>`<button type="button" class="chats-row" data-open-chat="${Number(c.post_id)}"><span class="chats-row-icon">✉</span><span class="chats-row-body"><strong>${esc(c.post_title)}</strong><small>${c.unread?`${c.unread} unread message${c.unread===1?'':'s'}`:'Open group conversation'}</small></span>${c.unread?`<span class="chats-row-unread">${c.unread>99?'99+':c.unread}</span>`:'<span class="chats-row-arrow">↗</span>'}</button>`).join('')}</div>`:'<div class="empty-state"><div class="empty-icon">✉</div><h3>No group chats yet</h3><p>Accept a study partner or join a team to start chatting.</p></div>');
}
function stopChatPoll(){if(chatPoll!==null){clearInterval(chatPoll);chatPoll=null;}currentChatPost=null;}
function ensureChatNotifications(){
 if($('chat-notification-trigger'))return;
 const area=$('user-actions');
 area.insertAdjacentHTML('afterbegin',`<div class="chat-notification-wrap"><button type="button" id="chat-notification-trigger" class="chat-notification-trigger" aria-label="Messages" title="Chat messages">✉ <span id="chat-notification-badge" class="chat-notification-badge" hidden>0</span></button><div id="chat-notification-panel" class="chat-notification-panel" hidden></div></div>`);
 $('chat-notification-trigger').onclick=()=>{const pane=$('chat-notification-panel');pane.hidden=!pane.hidden;if(!pane.hidden)checkChatNotifications(false);};
 $('chat-notification-panel').onclick=(e)=>{const target=e.target.closest('[data-notify-chat]');if(target){$('chat-notification-panel').hidden=true;openChat(Number(target.dataset.notifyChat));}};
}
async function checkChatNotifications(allowSound=true){
 if(!state.user || document.hidden || chatNotificationLoading)return;
 ensureChatNotifications();chatNotificationLoading=true;
 try {
  const info=await api('/api/chat/notifications');
  chatConversations=info.chats;
  const unread=info.chats.reduce((sum,c)=>sum+c.unread,0);
  const sideBadge=$('sidebar-chat-badge');
  if(sideBadge){sideBadge.textContent=unread>99?'99+':String(unread);sideBadge.hidden=unread===0;}
  if(state.section==='chats')renderChats();
  const badge=$('chat-notification-badge');badge.textContent=unread>99?'99+':String(unread);badge.hidden=unread===0;
  const panel=$('chat-notification-panel');
  panel.innerHTML=`<strong>Group messages</strong>${info.chats.length?info.chats.map(c=>`<button type="button" data-notify-chat="${c.post_id}"><span>${esc(c.post_title)}</span>${c.unread?`<b>${c.unread} new</b>`:'<small>Open chat</small>'}</button>`).join(''):'<p>No conversations yet.</p>'}`;
  const before=chatNotificationSnapshot;
  const now=Object.fromEntries(info.chats.map(c=>[c.post_id,c.latest_other_id]));
  const arrived=before!==null && info.chats.some(c=>c.unread && c.latest_other_id>(before[c.post_id]||0));
  if(allowSound && arrived){playNotice('incoming');toast('New group chat message! Click ✉ to open.');}
  chatNotificationSnapshot=now;
 }catch(e){console.warn('Chat notifications unavailable:',e);}finally{chatNotificationLoading=false;}
}
async function refreshChat(postId){
 if($('modal-overlay').hidden || !$('chat-messages') || currentChatPost!==postId){stopChatPoll();return;}
 try{
  const result=await api(`/api/posts/${postId}/messages`);const area=$('chat-messages');
  const bottom=area.scrollHeight-area.scrollTop-area.clientHeight<90;
  const previous=area.dataset.latestId||'0';
  const last=String(result.messages.at(-1)?.id||0);
  if(last!==previous){
   area.innerHTML=result.messages.length?result.messages.map(m=>`<div class="chat-message ${m.sender_id===state.user.id?'mine':''}"><div class="chat-sender">${avatar(m.sender_id,m.first_name,m.last_name,m.has_avatar)}<strong>${esc(m.first_name)} ${esc(m.last_name)}</strong></div><p>${esc(m.body)}</p><small>${esc(m.created_at)}</small></div>`).join(''):'<p class="chat-empty">No messages yet. Say hello to your team!</p>';
   area.dataset.latestId=last;if(bottom||previous==='0')area.scrollTop=area.scrollHeight;
  }
  await api(`/api/posts/${postId}/messages/read`,'POST',{});
  await checkChatNotifications(false);
 }catch(e){if($('chat-status'))$('chat-status').textContent=e.message;}
}
async function openChat(postId){
 stopChatPoll();currentChatPost=postId;
 modal(`<div class="chat-header"><span class="modal-symbol">✉</span><div><h2 id="modal-title">Group chat</h2><p class="modal-subtitle">Private to the organizer and accepted members</p></div></div><div id="chat-messages" class="chat-messages"></div><small id="chat-status"></small><form id="chat-form" class="chat-form"><textarea id="chat-input" placeholder="Write a message to your study group..." maxlength="1000" rows="2" required></textarea><button class="btn btn-primary" type="submit">Send ↗</button></form>`,true);
 document.querySelector('.modal').classList.add('chat-modal');
 $('chat-form').onsubmit=async(e)=>{e.preventDefault();const input=$('chat-input');const body=input.value.trim();if(!body)return;try{await api(`/api/posts/${postId}/messages`,'POST',{body});input.value='';await refreshChat(postId);}catch(err){toast(err.message,true);}};
 $('chat-input').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('chat-form').requestSubmit();}});
 await refreshChat(postId);chatPoll=setInterval(()=>refreshChat(postId),4000);
}
function closeModal(){stopChatPoll();$('modal-overlay').hidden=true;document.querySelector('.modal').classList.remove('chat-modal');$('modal-content').replaceChildren();}
const field=(label,name,type='text',placeholder='',extra='')=>`<div class="form-field"><label for="f-${name}">${label}</label><input id="f-${name}" name="${name}" type="${type}" placeholder="${placeholder}" ${extra} required></div>`;
function authModal(which='login',email='') {
 const icon='<span class="modal-symbol">✳</span>';
 if(which==='login') {
  modal(`${icon}<h2 id="modal-title">Welcome back!</h2><p class="modal-subtitle">Sign in with your university email to find a team.</p><form id="auth-form">${field('AITU email','email','email','student@astanait.edu.kz',`value="${esc(email)}" autocomplete="email"`)}${field('Password','password','password','Enter your password','autocomplete="current-password"')}<button type="submit" class="btn btn-primary form-btn">Sign in to StudyMate ↗</button></form><div class="form-switch">No account? <button data-auth="register">Sign up</button></div>`);
  $('auth-form').onsubmit=async(e)=>{e.preventDefault();const f=e.target;try{const d=await api('/api/login','POST',{email:f.email.value,password:f.password.value});state.csrf=d.csrf;closeModal();await refresh();toast('Welcome back!');}catch(err){showError(f,err);}};
 } else if(which==='register') {
  modal(`${icon}<h2 id="modal-title">Join your community</h2><p class="modal-subtitle">StudyMate is available only to AITU students. Email verification is required.</p><form id="auth-form"><div class="form-grid">${field('First name','first_name','text','Aydana','autocomplete="given-name" maxlength="50"')}${field('Last name','last_name','text','Serikova','autocomplete="family-name" maxlength="50"')}</div>${field('University email','email','email','name@astanait.edu.kz','autocomplete="email"')}${field('Password','password','password','At least 10 characters','autocomplete="new-password" minlength="10"')}<p class="field-hint">Your password must contain letters and numbers.</p><label class="consent-label"><input type="checkbox" name="consent" required><span>I consent to processing my name and university email for my account, and optional profile information I choose to provide. My first and last name will be visible to other users in posts.</span></label><button type="submit" class="btn btn-primary form-btn">Create account ↗</button></form><div class="form-switch">Already have an account? <button data-auth="login">Sign in</button></div>`);
  $('auth-form').onsubmit=async(e)=>{e.preventDefault();const f=e.target;const d=Object.fromEntries(new FormData(f));d.consent=f.elements.consent.checked;try{await api('/api/register','POST',d);authModal('verify',d.email);toast('Verification code sent!');}catch(err){showError(f,err);}};
 } else {
  modal(`${icon}<h2 id="modal-title">Verify your email</h2><p class="modal-subtitle">We sent a six-digit code to <strong>${esc(email)}</strong>. In local demo mode, the code is printed in the server terminal.</p><form id="auth-form"><input type="hidden" name="email" value="${esc(email)}">${field('Email verification code','code','text','000000','inputmode="numeric" pattern="[0-9]{6}" maxlength="6"')}<button type="submit" class="btn btn-primary form-btn">Verify and sign in ↗</button></form><div class="form-switch"><button id="resend-code">Resend code</button> · <button data-auth="login">Back to sign in</button></div>`);
  $('auth-form').onsubmit=async(e)=>{e.preventDefault();const f=e.target;try{const d=await api('/api/verify','POST',{email,code:f.code.value});state.csrf=d.csrf;closeModal();await refresh();toast('Email verified. Welcome!');}catch(err){showError(f,err);}};
  $('resend-code').onclick=async()=>{try{const d=await api('/api/resend-code','POST',{email});toast(d.message);}catch(err){toast(err.message,true);}};
 }
}
function setPage(){
 const auth=!!state.user;$('landing').hidden=auth;$('dashboard').hidden=!auth;$('guest-actions').hidden=auth;$('user-actions').hidden=!auth;$('avatar-btn').innerHTML=auth?avatar(state.user.id,state.user.first_name,state.user.last_name,state.user.has_avatar):'A';
 if(auth){$('dash-title').textContent=`Hello, ${state.user.first_name}! ✳`;}
}
async function refresh(){
 const session=await api('/api/me');state.user=session.user;state.csrf=session.csrf;setPage();
 if(state.user){ensureChatNotifications();await checkChatNotifications(false);$('moderation-nav').hidden=state.user.role!=='moderator';await Promise.all([loadSubjects(),loadStats()]);await showSection(state.section);await checkNotifications();}else {state.section='explore';chatConversations=[];notificationSeen.userId=null;notificationSeen.received=null;notificationSeen.sent=null;chatNotificationSnapshot=null;}
}
async function loadSubjects(){const d=await api('/api/subjects');state.subjects=d.subjects;$('subject-filter').innerHTML='<option value="">All subjects</option>'+d.subjects.map(x=>`<option value="${x.id}">${esc(x.name)}</option>`).join('');}
async function loadStats(){const d=await api('/api/stats');$('stat-posts').textContent=d.posts;$('stat-users').textContent=d.students;$('stat-connections').textContent=d.connections;await loadAnalytics();}
async function loadAnalytics(){
 const d=await api('/api/analytics');
 const makeBars=(rows)=>rows.map(r=>`<div class="analytics-row"><span title="${esc(r.label)}">${esc(r.label)}</span><div class="analytics-track"><div style="width:${Math.round(100*r.count/Math.max(1,...rows.map(x=>x.count)))}%"></div></div><strong>${r.count}</strong></div>`).join('');
 $('analytics-subjects').innerHTML=makeBars(d.subjects);
 $('analytics-categories').innerHTML=makeBars(d.categories);
 $('analytics-applications').innerHTML=makeBars(d.applications);
}

function emptyState(heading,message,canCreate=false){return `<div class="empty-state"><div class="empty-icon">✳</div><h3>${esc(heading)}</h3><p>${esc(message)}</p>${canCreate?'<button class="btn btn-primary" data-create="1">+ Create post</button>':''}</div>`;}
async function showSection(section){
 state.section=section;
 document.querySelectorAll('[data-section]').forEach(b=>b.classList.toggle('selected',b.dataset.section===section));
 const isFeed=section==='explore'||section==='mine';$('feed-controls').hidden=!isFeed;
 if(isFeed){$('feed-title').textContent=section==='mine'?'My posts':'All posts';await loadPosts();}
 else if(section==='chats'){await checkChatNotifications(false);renderChats();}
 else if(section==='moderation') await loadReports();
 else await loadApplications(section);
}
async function loadPosts(){
 try{const qp=new URLSearchParams();if(state.section==='mine')qp.set('mine','1');if(state.subject)qp.set('subject',state.subject);if(state.category)qp.set('category',state.category);if(state.query)qp.set('q',state.query);if(state.deadline)qp.set('deadline',state.deadline);
 const d=await api('/api/posts?'+qp.toString());state.posts=d.posts;$('results-label').textContent=d.posts.length+' requests';
 $('content-list').innerHTML=d.posts.length?d.posts.map(postCard).join(''):emptyState('No results yet','Try another subject or create your own post.',state.section==='explore');
 }catch(err){toast(err.message,true);}
}
function postCard(p){
 const own=p.author_id===state.user.id, full=p.joined>=p.max_partners;
 const color=p.category==='project'?'orange':p.category==='midterm'?'green':p.status==='closed'?'gray':'';
 const reply=own?(p.status==='open'?`<button class="btn btn-outline card-action" data-edit-deadline="${p.id}">Edit deadline</button> <button class="btn btn-outline card-action" data-close="${p.id}">Close post</button>`:'<span class="status declined">Closed</span>'):
   p.my_status?`<span class="status ${p.my_status}">${{pending:'Application sent',accepted:'Accepted ✓',declined:'Declined'}[p.my_status]}</span>`:
   full?'<span class="status declined">Team is full</span>':`<button class="btn btn-primary card-action" data-apply="${p.id}">Apply ↗</button>`;
 const report=own?'':` <button class="btn btn-outline card-action" data-report="${p.id}">Report</button>`;
 return `<article class="post-card"><div class="post-head"><div class="profile-chip">${avatar(p.author_id,p.first_name,p.last_name,p.has_avatar)}<span><b>${esc(p.first_name)} ${esc(p.last_name)}</b><small>${esc(dateText(p.created_at))} · AITU student</small></span></div><span class="post-tag ${color}">${esc(categories[p.category])}</span></div><h3>${esc(p.title)}</h3><p class="post-description">${esc(p.description)}</p><div class="card-chips"><span class="chip subject">▤ ${esc(p.subject)}</span><span class="chip">◷ ${esc(formats[p.meeting_format])}</span>${p.deadline?`<span class="chip">Due: ${esc(p.deadline)}</span>`:''}<span class="chip">${p.intent==='offer'?'✦ I can help':'✦ Looking for a partner'}</span></div><div class="card-footer"><span class="spots">◎ <strong>${p.joined} / ${p.max_partners}</strong> partners</span><div class="card-actions">${reply}${report}</div></div></article>`;
}
async function loadApplications(section){
 const scope=section==='sent'?'sent':'received';$('content-list').innerHTML=emptyState('Loading applications','Connecting to the database.');
 try{const d=await api('/api/applications?scope='+scope);$('content-list').innerHTML=d.applications.length?d.applications.map(a=>applicationCard(a,scope)).join(''):emptyState(scope==='received'?'No applications yet':'You have not applied yet',scope==='received'?'When someone applies to join your post, their message will appear here.':'Find a post and send your first application!');}
 catch(err){toast(err.message,true);}
}
async function loadReports(){
 $('content-list').innerHTML=emptyState('Loading reports','Checking moderation queue.');
 try{const d=await api('/api/mod/reports');$('content-list').innerHTML=d.reports.length?d.reports.map(r=>`<article class="application-card"><h3>${esc(r.post_title)}</h3><p>${esc(r.reason)}</p><p>Reported ${esc(r.created_at)} · ${esc(r.status)}</p>${r.status==='open'?`<button class="btn btn-danger card-action" data-mod-remove="${r.id}">Remove post</button> <button class="btn btn-outline card-action" data-mod-block="${r.author_id}">Block author</button>`:''}</article>`).join(''):emptyState('No reports','No reported posts.');}
 catch(e){toast(e.message,true);}
}
function applicationCard(a,scope){
 const available=a.joined<a.max_partners;
 const actions=scope==='received'&&a.status==='pending'&&a.post_status==='open'?`<div class="app-buttons"><button class="btn btn-danger card-action" data-decision="${a.id}" data-value="declined">Decline</button><button class="btn btn-primary card-action" data-decision="${a.id}" data-value="accepted" ${available?'':'disabled'}>Accept ✓</button></div>`:'';
 const chat=a.status==='accepted'?`<button class=\"btn btn-outline card-action\" data-open-chat=\"${a.post_id}\">Open group chat ↗</button>`:'';
 const statusTxt={pending:'Pending',accepted:'Accepted',declined:'Declined'};
 return `<article class="application-card"><div class="application-meta"><span class="status ${a.status}">${statusTxt[a.status]}</span><span>▤ ${esc(a.subject)}</span><span>${esc(dateText(a.created_at))}</span></div><h3>${esc(a.post_title)}</h3>${scope==='received'?`<div class="profile-chip">${avatar(a.applicant_id,a.first_name,a.last_name,a.applicant_has_avatar)}<b>${esc(a.first_name)} ${esc(a.last_name)}</b></div>`:''}<p>“${esc(a.message)}”</p>${a.contact_email?`<p class="contact-disclosure">Contact unlocked: ${esc(a.contact_email)}${a.contact_telegram?` · Telegram: ${esc(a.contact_telegram)}`:""}</p>`:""}${actions}${chat}</article>`;
}
function createModal(){
 const options=state.subjects.map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');
 modal(`<span class="modal-symbol">✎</span><h2 id="modal-title">New study post</h2><p class="modal-subtitle">Create a partner request using five main fields. Optional settings are below.</p>
 <form id="create-form">
 <div class="form-grid"><div class="form-field"><label for="f-subject_id">1. Subject</label><select name="subject_id" id="f-subject_id" required><option value="">Choose a subject</option>${options}</select></div>
 <div class="form-field"><label for="f-category">2. Work type</label><select name="category" id="f-category" required><option value="project">Project</option><option value="group">Group work</option><option value="midterm">Midterm</option><option value="endterm">Endterm</option><option value="homework">Homework</option><option value="study">Study together</option></select></div></div>
 <div class="form-grid"><div class="form-field"><label for="f-deadline">3. Deadline</label><input id="f-deadline" type="date" name="deadline" required></div>
 <div class="form-field"><label for="f-max_partners">4. Free places</label><select id="f-max_partners" name="max_partners" required>${Array.from({length:5},(_,i)=>`<option value="${i+1}" ${i===1?'selected':''}>${i+1}</option>`).join('')}</select></div></div>
 <div class="form-field"><label for="f-description">5. Description</label><textarea id="f-description" name="description" minlength="10" maxlength="1000" placeholder="Describe the assignment and what you need from a study partner..." required></textarea></div>
 <details class="advanced-post"><summary>Optional settings (title, meeting format, request type)</summary>
 <div class="form-field"><label for="f-title">Custom title (optional)</label><input id="f-title" name="title" maxlength="100" placeholder="Generated automatically if empty"></div>
 <div class="form-grid"><div class="form-field"><label for="f-meeting_format">Meeting format</label><select name="meeting_format" id="f-meeting_format"><option value="either">Any</option><option value="online">Online</option><option value="offline">In person</option></select></div>
 <div class="form-field"><label for="f-intent">Request type</label><select name="intent" id="f-intent"><option value="seek">Looking for partners</option><option value="offer">Offering help</option></select></div></div></details>
 <button type="submit" class="btn btn-primary form-btn">Publish post ↗</button></form>`,true);
 const today=new Date();$('f-deadline').min=[today.getFullYear(),String(today.getMonth()+1).padStart(2,'0'),String(today.getDate()).padStart(2,'0')].join('-');
 $('create-form').onsubmit=async(e)=>{e.preventDefault();const f=e.target;try{
   const payload=Object.fromEntries(new FormData(f));payload.subject_id=+payload.subject_id;payload.max_partners=+payload.max_partners;
   const subjectName=state.subjects.find(x=>x.id===payload.subject_id)?.name || 'study';
   payload.title=payload.title?.trim() || ('Study partners for '+subjectName).slice(0,100);
   payload.meeting_format=payload.meeting_format||'either';payload.intent=payload.intent||'seek';
   await api('/api/posts','POST',payload);closeModal();await loadStats();await showSection('mine');toast('Post published — find your team!');
 }catch(err){showError(f,err);}};
}
function editDeadlineModal(id){
 const p=state.posts.find(x=>x.id===id && x.author_id===state.user?.id && x.status==='open');
 if(!p){toast('Open your own post to change its deadline.',true);return;}
 modal(`<span class="modal-symbol">▦</span><h2 id="modal-title">Edit post deadline</h2><p class="modal-subtitle">${esc(p.title)}</p><form id="edit-deadline-form"><div class="form-field"><label for="new-deadline">New deadline</label><input type="date" id="new-deadline" name="deadline" required value="${esc(p.deadline||'')}"></div><button class="btn btn-primary form-btn" type="submit">Save deadline</button></form>`);
 const d=$('new-deadline');const today=new Date();d.min=[today.getFullYear(),String(today.getMonth()+1).padStart(2,'0'),String(today.getDate()).padStart(2,'0')].join('-');
 $('edit-deadline-form').onsubmit=async(e)=>{
  e.preventDefault();try{
   await api(`/api/posts/${id}/deadline`,'POST',{deadline:d.value});
   closeModal();await loadPosts();toast('Deadline updated.');
  }catch(err){showError(e.target,err);}
 };
}
function applyModal(id){const p=state.posts.find(x=>x.id===id);if(!p)return;modal(`<span class="modal-symbol">↗</span><h2 id="modal-title">Apply to post</h2><p class="modal-subtitle"><strong>${esc(p.title)}</strong><br>Tell ${esc(p.first_name)} ${esc(p.last_name)}, why you want to join.</p><form id="apply-form"><div class="form-field"><label for="f-message">Message to the organizer</label><textarea name="message" id="f-message" minlength="5" maxlength="500" placeholder="Hi! I am also studying this subject..." required></textarea></div><button type="submit" class="btn btn-primary form-btn">Send application ↗</button></form>`);$('apply-form').onsubmit=async(e)=>{e.preventDefault();try{await api(`/api/posts/${id}/apply`,'POST',{message:e.target.message.value});closeModal();await loadPosts();toast('Application sent!');playNotice('sent');}catch(err){showError(e.target,err);}};}
function profileModal(){
 const u=state.user;
 modal(`<span class="modal-symbol">✳</span><h2 id="modal-title">My profile</h2><p class="modal-subtitle">${esc(u.email)} · Verified AITU email</p><form id="profile-form"><div class="form-grid">${field('First name','first_name','text','','value="'+esc(u.first_name)+'" maxlength="50"')}${field('Last name','last_name','text','','value="'+esc(u.last_name)+'" maxlength="50"')}</div>${field('Course / year','course','text','SE-1','value="'+esc(u.course||'')+'" maxlength="60"')}${field('Subjects','subjects_text','text','Calculus, ICT','value="'+esc(u.subjects_text||'')+'" maxlength="120"')}${field('Preferred study time','preferred_time','text','Weekdays after 5 PM','value="'+esc(u.preferred_time||'')+'" maxlength="80"')}${field('Telegram username (optional)','telegram','text','@username','value="'+esc(u.telegram||'')+'" maxlength="64"')}<div class="avatar-upload"><div id="avatar-preview">${avatar(u.id,u.first_name,u.last_name,u.has_avatar)}</div><div><label for="avatar-file" class="avatar-label">Upload avatar</label><input id="avatar-file" type="file" accept="image/png,image/jpeg,image/webp"><p class="field-hint">JPG, PNG or WebP. Automatically resized; up to 600 KB stored.</p><button type="button" class="btn btn-outline" id="remove-avatar">Remove photo</button></div></div><button type="submit" class="btn btn-primary form-btn">Save profile ↗</button></form><div class="form-switch"><button id="logout-button">Sign out</button></div>`);
 $('profile-form').onsubmit=async(e)=>{e.preventDefault();try{await api('/api/profile','POST',Object.fromEntries(new FormData(e.target)));closeModal();await refresh();toast('Profile updated.');}catch(err){showError(e.target,err);}};
 $('avatar-file').onchange=async(e)=>{
  const file=e.target.files?.[0];if(!file)return;
  if(!['image/jpeg','image/png','image/webp'].includes(file.type)||file.size>12*1024*1024){toast('Choose a JPG, PNG or WebP under 12 MB.',true);return;}
  try{
   const img=new Image();const source=URL.createObjectURL(file);
   try{await new Promise((resolve,reject)=>{img.onload=resolve;img.onerror=()=>reject(new Error('Cannot read this image.'));img.src=source;});}finally{URL.revokeObjectURL(source);}
   let encoded='';
   for(const dim of [512,384,256,192,128]){
    const scale=Math.min(1,dim/Math.max(img.width,img.height));
    const canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(img.width*scale));canvas.height=Math.max(1,Math.round(img.height*scale));
    canvas.getContext('2d').drawImage(img,0,0,canvas.width,canvas.height);
    for(const quality of [.86,.72,.55]){
     encoded=canvas.toDataURL('image/jpeg',quality);
     if(encoded.length<780000)break;
    }
    if(encoded.length<780000)break;
   }
   if(encoded.length>=780000)throw new Error('The image could not be compressed. Try another photo.');
   await api('/api/avatar','POST',{image:encoded});
   avatarVersion[state.user.id]=Date.now();
   toast('Avatar saved successfully!');closeModal();await refresh();
  }catch(err){toast('Avatar upload failed: '+err.message,true);}
 };
 $('remove-avatar').onclick=async()=>{try{await api('/api/avatar','POST',{image:null});closeModal();await refresh();toast('Avatar removed.');}catch(err){toast(err.message,true);}};
 $('logout-button').onclick=async()=>{try{await api('/api/logout','POST',{});closeModal();await refresh();toast('Signed out.');}catch(err){toast(err.message,true);}};
}
$('login-top').onclick=()=>authModal('login');$('signup-top').onclick=()=>authModal('register');$('hero-start').onclick=()=>authModal('register');$('brand-btn').onclick=()=>{if(state.user)showSection('explore');else window.scrollTo({top:0,behavior:'smooth'});};
$('modal-close').onclick=closeModal;$('modal-overlay').addEventListener('click',(e)=>{if(e.target===$('modal-overlay'))closeModal();});document.addEventListener('keydown',(e)=>{if(e.key==='Escape'&&!$('modal-overlay').hidden)closeModal();});
$('modal-content').addEventListener('click',(e)=>{const b=e.target.closest('[data-auth]');if(b)authModal(b.dataset.auth);});
$('new-post-main').onclick=createModal;$('side-create').onclick=createModal;$('avatar-btn').onclick=profileModal;
document.querySelectorAll('[data-section]').forEach(b=>b.onclick=()=>showSection(b.dataset.section));
let searchTimer;$('search-input').oninput=e=>{state.query=e.target.value.trim();clearTimeout(searchTimer);searchTimer=setTimeout(()=>loadPosts(),250);};$('subject-filter').onchange=e=>{state.subject=e.target.value;loadPosts();};$('category-filter').onchange=e=>{state.category=e.target.value;loadPosts();};$('deadline-filter').onchange=e=>{state.deadline=e.target.value;loadPosts();};
$('content-list').addEventListener('click',async(e)=>{
 const create=e.target.closest('[data-create]');if(create)return createModal();
 const apply=e.target.closest('[data-apply]');if(apply)return applyModal(+apply.dataset.apply);
 const report=e.target.closest('[data-report]');if(report){const reason=prompt('Describe the problem with this post (10-500 characters):');if(reason===null)return;try{await api(`/api/posts/${report.dataset.report}/report`,'POST',{reason});toast('Report sent to moderators.');}catch(err){toast(err.message,true);}return;}
 const remove=e.target.closest('[data-mod-remove]');if(remove){if(!confirm('Remove this reported post?'))return;try{await api(`/api/mod/reports/${remove.dataset.modRemove}/remove`,'POST',{});await loadReports();toast('Post removed.');}catch(err){toast(err.message,true);}return;}
 const block=e.target.closest('[data-mod-block]');if(block){if(!confirm('Block this account?'))return;try{await api(`/api/mod/users/${block.dataset.modBlock}/block`,'POST',{blocked:true});await loadReports();toast('Account blocked.');}catch(err){toast(err.message,true);}return;}
 const edit=e.target.closest('[data-edit-deadline]');if(edit)return editDeadlineModal(+edit.dataset.editDeadline);
 const close=e.target.closest('[data-close]');if(close){if(!confirm('Close this post?'))return;try{await api(`/api/posts/${close.dataset.close}/close`,'POST',{});await loadStats();await loadPosts();toast('Post closed.');}catch(err){toast(err.message,true);}return;}
 const chat=e.target.closest('[data-open-chat]');if(chat)return openChat(+chat.dataset.openChat);
 const choice=e.target.closest('[data-decision]');if(choice){try{await api(`/api/applications/${choice.dataset.decision}/decision`,'POST',{status:choice.dataset.value});await loadStats();await loadApplications(state.section);toast(choice.dataset.value==='accepted'?'Applicant accepted!':'Application declined.');playNotice('sent');}catch(err){toast(err.message,true);}}
});
$('notification-sounds').addEventListener('change',e=>setSoundEnabled(e.target.checked));
setInterval(checkNotifications,5000);
setInterval(()=>checkChatNotifications(true),5000);
refresh().catch(err=>toast(err.message,true));
