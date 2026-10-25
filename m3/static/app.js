'use strict';
// 令牌仅保存在内存；刷新或切换账号必须重新登录，不能通过前端切角色绕过认证。
const state = {token: '', user: null, page: 'tickets', tickets: [], total: 0, offset: 0,
  filter: '', selected: null, rooms: [], users: [], notifications: [], audits: [], modules: [], busy: false};
const labels = {pending:'待派单', starting:'下架同步中', repairing:'维修中', awaiting_acceptance:'待验收', closing:'恢复同步中', closed:'已关闭'};
const roles = {student:'学生', approver:'审批人', technician:'维修人员', admin:'管理员'};
const $ = (s) => document.querySelector(s);
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const date = value => value ? esc(value.replace('T',' ').slice(0,16)) : '—';
const allowed = p => state.user?.permissions.includes(p);
const badge = t => `<span class="tag ${t.state === 'closed' ? 'good' : ['starting','closing'].includes(t.state) ? 'warn' : ''}">${labels[t.state] || esc(t.state)}</span>`;
let noticeTimer;
function notice(message, error=false) { const n=$('#notice'); n.textContent=message; n.className=error?'error':''; n.hidden=false; clearTimeout(noticeTimer); noticeTimer=setTimeout(()=>n.hidden=true,6000); }
async function api(path, options={}) {
  const response=await fetch(path,{...options,headers:{'Content-Type':'application/json', ...(state.token?{Authorization:`Bearer ${state.token}`}:{})}});
  const data=await response.json();
  if (!response.ok) {
    if(response.status===401 && state.token){state.token='';state.user=null;$('#modal').close();render();}
    throw new Error(typeof data.detail==='string'?data.detail:'输入无效，请检查必填字段和长度');
  }
  return data;
}
const post=(url,data={})=>api(url,{method:'POST',body:JSON.stringify(data)});
function render() {
  if(!state.user){$('#app').innerHTML=`<main class="login"><section class="login-art"><div class="brand">栖学<small>QIXUE / CAMPUS SPACE</small></div><div><div class="plan" aria-hidden="true">${'<i></i>'.repeat(12)}</div><h1>让每一处空间，<br>回到好用的状态。</h1><p>从一次报修，到一次安心使用。<br>让维修进度可见，让每个问题有回应。</p></div><footer class="eyebrow">SPACE SERVICE DESK · M3</footer></section><section class="login-form"><form id="login-form"><div class="eyebrow">WELCOME BACK</div><h1>登录空间服务台</h1><p class="muted">使用管理员为你开通的账号</p><label>账号<input name="username" autocomplete="username" maxlength="64" required></label><label>密码<input name="password" type="password" autocomplete="current-password" maxlength="128" required></label><button class="primary" style="width:100%">登录</button><p class="muted" style="font-size:12px;margin-top:24px">角色权限由账号决定。切换身份时，请退出并使用对应账号重新登录。</p></form></section></main>`; return;}
  const nav=[['tickets','工单工作台'],['notifications','站内通知'],...(allowed('users:manage')?[['users','用户与权限']]:[]),...(allowed('audit:read')?[['audit','操作审计']]:[])];
  $('#app').innerHTML=`<div class="shell"><aside class="sidebar"><div class="brand">栖学<small>SPACE SERVICE DESK</small></div><nav aria-label="主导航">${nav.map(([key,name])=>`<button data-nav="${key}" class="${state.page===key?'active':''}">${name}</button>`).join('')}</nav><div class="foot">校园空间 · 物业服务<br>每一步处理，都有迹可循。<br><button data-action="password">修改密码</button><br><button data-action="logout">退出 / 切换账号</button></div></aside><main class="main"><header class="topbar row between"><span class="eyebrow" style="margin:0">栖学 / 公共服务 / ${nav.find(n=>n[0]===state.page)?.[1] || ''}</span><div class="row"><span class="avatar">${esc(state.user.display_name.slice(0,1))}</span><span>${esc(state.user.display_name)} · ${roles[state.user.role]}</span><button class="small mobile-only" data-action="logout">退出</button></div></header><div id="content"></div></main></div>`;
  renderContent();
  const navElement = document.querySelector('.sidebar nav');
  if(navElement){
    const section=document.createElement('div'); section.className='module-links';
    section.innerHTML='<div class="eyebrow" style="margin:22px 0 10px">模块入口</div>'+state.modules.map(m=>m.url
      ?`<a href="${esc(m.url)}" target="_blank" rel="noopener noreferrer" title="${esc(m.note)}">${esc(m.name)} ↗</a>`
      :`<span title="${esc(m.note)}">${esc(m.name)} · 待接入</span>`).join('');
    navElement.append(section);
  }
}
function renderContent(){
  const content=$('#content'); if(!content)return;
  if(state.page==='tickets'){
    const closed=state.tickets.filter(t=>t.state==='closed').length;
    content.innerHTML=`<section class="hero row between wrap"><div><div class="eyebrow">MAINTENANCE / WORKSPACE</div><h1>${state.user.role==='technician'?'我的维修任务':'工单工作台'}</h1><p>看见问题，跟进处理，让空间恢复如常。</p></div>${allowed('tickets:create')?'<button class="primary" data-action="create">＋ 提交报修</button>':''}</section><div class="metrics"><div class="metric"><span>符合筛选的工单</span><b>${state.total}</b><span>按当前账号权限统计</span></div><div class="metric"><span>本页处理中</span><b>${state.tickets.length-closed}</b><span>等待处理与维修跟进</span></div><div class="metric"><span>本页超时升级</span><b>${state.tickets.filter(t=>t.escalated&&t.state!=='closed').length}</b><span>已通知管理员关注</span></div></div><div class="toolbar row between"><div class="row"><h2 style="margin:0">工单记录</h2><select id="state-filter" aria-label="按状态筛选" style="width:auto"><option value="">全部状态</option>${Object.entries(labels).map(([k,v])=>`<option value="${k}" ${state.filter===k?'selected':''}>${v}</option>`).join('')}</select></div><button class="small" data-action="refresh">刷新</button></div><div class="${state.selected?'split':''}"><div><div class="panel"><div class="list-head"><span>报修空间 / 问题</span><span>状态</span><span class="date">提交时间</span><span>操作</span></div>${state.tickets.length?state.tickets.map(t=>`<div class="ticket-row"><div><strong>${esc(t.room_name)}</strong><p class="muted">${esc(t.description.slice(0,45))}${t.description.length>45?'…':''}</p></div><div>${badge(t)}${t.escalated&&t.state!=='closed'?'<br><span class="tag error" style="margin-top:6px">超时升级</span>':''}</div><span class="muted date">${date(t.created)}</span><button class="small" data-ticket="${esc(t.id)}">详情</button></div>`).join(''):'<div class="empty">暂无符合条件的工单。<br>提交报修后，可在这里跟进处理进度。</div>'}</div><div class="row between pager"><button class="small" data-action="prev" ${state.offset===0?'disabled':''}>上一页</button><span class="muted">${state.total?state.offset+1:0}—${Math.min(state.offset+20,state.total)} / ${state.total}</span><button class="small" data-action="next" ${state.offset+20>=state.total?'disabled':''}>下一页</button></div></div>${state.selected?detailView(state.selected):''}</div>`;
  }else if(state.page==='notifications'){
    content.innerHTML=`<div class="hero"><div class="eyebrow">INBOX</div><h1>站内通知</h1><p>报修、派单和验收的最新进展。显示最近 100 条。</p></div><button data-action="refresh" class="small" style="margin-bottom:18px">刷新</button><div class="panel">${state.notifications.length?state.notifications.map(n=>`<article class="message"><div class="row between"><span class="tag">${esc(n.source)} · ${n.read_at?'已读':'未读'}</span><time class="muted">${date(n.created)}</time></div><p>${esc(n.text)}</p>${!n.read_at?`<button class="small" data-read="${n.id}">标记已读</button>`:''}</article>`).join(''):'<div class="empty">目前没有通知</div>'}</div>`;
  }else if(state.page==='users'){
    content.innerHTML=`<div class="hero row between"><div><div class="eyebrow">ACCESS CONTROL</div><h1>用户与权限</h1><p>真实账号登录，按职责分配权限。</p></div><button class="primary" data-action="create-user">添加账号</button></div><div class="panel table-wrap"><table><thead><tr><th>账号</th><th>姓名</th><th>角色</th><th>状态</th><th>管理</th></tr></thead><tbody>${state.users.map(u=>`<tr><td>${esc(u.username)}</td><td>${esc(u.display_name)}</td><td>${roles[u.role]}</td><td>${u.active?'启用':'停用'}</td><td>${u.id!==state.user.id?`<button class="small" data-user="${esc(u.id)}">调整权限</button>`:'当前账号'}</td></tr>`).join('')}</tbody></table></div>`;
  }else{
    content.innerHTML=`<div class="hero"><div class="eyebrow">OPERATION LOG</div><h1>操作审计</h1><p>关键操作留痕；显示最近 100 条，完整记录可通过 API 分页查询。</p></div><button data-action="refresh" class="small" style="margin-bottom:18px">刷新</button><div class="panel table-wrap"><table><thead><tr><th>时间</th><th>来源</th><th>操作者</th><th>动作</th><th>对象</th></tr></thead><tbody>${state.audits.map(a=>`<tr><td>${date(a.created)}</td><td>${esc(a.source)}</td><td>${esc(state.users.find(u=>u.id===a.actor)?.display_name||a.actor)}</td><td>${esc(a.action)}</td><td>${esc(a.target)}</td></tr>`).join('')}</tbody></table></div>`;
  }
}
function detailView(t){
  const failed=t.sync_jobs.find(j=>['retry','failed'].includes(j.status));
  return `<aside class="panel"><div class="detail-head"><div class="row between"><span class="eyebrow" style="margin:0">工单 ${esc(t.id.slice(0,8))}</span><button class="small" data-action="close-detail" aria-label="关闭详情">关闭</button></div><h2 style="margin:20px 0 12px">${esc(t.room_name)}</h2>${badge(t)}</div><div class="detail-body"><p>${esc(t.description)}</p><dl><dt>提交时间</dt><dd>${date(t.created)}</dd><dt>处理时限</dt><dd>${date(t.due)}</dd><dt>维修人员</dt><dd>${esc(state.users.find(u=>u.id===t.assignee_id)?.display_name || (t.assignee_id?'已指派':'未指派'))}</dd></dl>${failed?`<div class="alert">同步未完成：${esc(failed.last_error)}<br>已尝试 ${failed.attempts} 次。${failed.status==='retry'?'后台将自动重试。':'请协调解决后手动重试。'}</div>`:''}<div class="row wrap">${allowed('tickets:assign')&&['pending','repairing'].includes(t.state)?'<button class="primary" data-action="assign">派单 / 转派</button>':''}${allowed('tickets:repair')&&t.assignee_id===state.user.id&&t.state==='repairing'?'<button class="primary" data-action="complete">提交完工</button>':''}${allowed('tickets:accept')&&t.state==='awaiting_acceptance'?'<button class="primary" data-action="accept">验收工单</button>':''}${t.reporter_id===state.user.id&&t.state==='closed'&&t.rating===null?'<button class="primary" data-action="review">评价服务</button>':''}${allowed('tickets:retry')&&failed?'<button data-action="retry">重试同步</button>':''}</div>${t.rating?`<p style="margin-top:24px">服务评价：${t.rating} / 5<br>${esc(t.review)}</p>`:''}<h3 style="margin-top:28px">处理轨迹</h3><ol class="timeline">${(t.history||[]).map(h=>`<li><time>${date(h.created)}</time>${esc(h.note)}</li>`).join('')}</ol></div></aside>`;
}
async function load(){
  if(state.page==='tickets'){
    const data=await api(`/api/m3/tickets?limit=20&offset=${state.offset}&state=${encodeURIComponent(state.filter)}`);
    state.tickets=data.items; state.total=data.total;
    if(state.selected)state.selected=await api(`/api/m3/tickets/${state.selected.id}`);
  } else if(state.page==='notifications') state.notifications=await api('/api/notifications?limit=100');
  else if(state.page==='users')state.users=await api('/api/users');
  else state.audits=await api('/api/audit?limit=100');
  renderContent();
}
function modal(title,body,id){$('#modal-body').innerHTML=`<div class="row between"><h2 style="margin:0">${title}</h2><button type="button" class="small" data-action="close-modal">取消</button></div><form id="${id}">${body}<button class="primary">确认提交</button></form>`;$('#modal').showModal();}
async function action(name){
  if(name==='close-modal')return $('#modal').close();
  if(name==='close-detail'){state.selected=null;return renderContent();}
  if(name==='refresh')return load();
  if(name==='prev'||name==='next'){state.offset+=name==='next'?20:-20;return load();}
  if(name==='logout'){await post('/api/auth/logout');state.token='';state.user=null;state.selected=null;return render();}
  if(name==='password')return modal('修改密码','<label>原密码<input type="password" name="old_password" autocomplete="current-password" required></label><label>新密码<input type="password" name="new_password" minlength="12" maxlength="128" autocomplete="new-password" required></label>','password-form');
  if(name==='create'){
    state.rooms=await api('/api/m3/rooms');
    return modal('提交报修',`<label>教室<select name="room_id" required><option value="">选择报修教室</option>${state.rooms.filter(r=>r.active).map(r=>`<option value="${r.id}">${esc(r.name)} · ${esc(r.area)}</option>`).join('')}</select></label><label>故障描述<textarea name="description" minlength="5" maxlength="2000" placeholder="请描述位置、故障现象和影响，至少 5 字" required></textarea></label><p class="muted">维修范围按整间教室处理，请勿填写与报修无关的个人信息。</p>`,'create-form');
  }
  if(name==='assign'){
    state.users=await api('/api/users');
    return modal('指派维修人员',`<label>维修人员<select name="assignee_id" required><option value="">请选择</option>${state.users.filter(u=>u.role==='technician'&&u.active).map(u=>`<option value="${u.id}">${esc(u.display_name)}</option>`).join('')}</select></label><p class="muted">首次派单后会同步下架教室。若已有预约冲突，需要先协调处理。</p>`,'assign-form');
  }
  if(name==='complete')return modal('提交维修结果','<label>维修记录<textarea name="note" minlength="2" maxlength="2000" required placeholder="处理过程、更换内容与检查结果"></textarea></label>','complete-form');
  if(name==='accept')return modal('验收工单','<label>验收结论<select name="approved"><option value="true">通过，恢复教室可用性</option><option value="false">驳回，继续维修</option></select></label><label>验收说明<textarea name="note" minlength="2" maxlength="2000" required></textarea></label>','accept-form');
  if(name==='review')return modal('评价维修服务','<label>评分<select name="rating"><option value="5">5 · 非常满意</option><option value="4">4 · 满意</option><option value="3">3 · 一般</option><option value="2">2 · 不满意</option><option value="1">1 · 很不满意</option></select></label><label>评价（选填）<textarea name="comment" maxlength="1000"></textarea></label>','review-form');
  if(name==='retry'){await post(`/api/m3/tickets/${state.selected.id}/retry`);notice('已提交重试');return load();}
  if(name==='create-user')return modal('添加账号',`<label>账号<input name="username" pattern="[a-zA-Z0-9_.-]+" maxlength="64" required></label><label>姓名<input name="display_name" maxlength="80" required></label><label>角色<select name="role">${Object.entries(roles).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label><label>初始密码<input type="password" name="password" minlength="12" maxlength="128" autocomplete="new-password" required></label>`,'user-form');
}
document.addEventListener('click',async e=>{
  const el=e.target.closest('button');if(!el||el.disabled)return;
  try{
    if(el.dataset.nav){state.page=el.dataset.nav;render();await load();}
    else if(el.dataset.ticket){state.selected=await api(`/api/m3/tickets/${el.dataset.ticket}`);renderContent();}
    else if(el.dataset.read){await post(`/api/notifications/${el.dataset.read}/read`);await load();}
    else if(el.dataset.user){const u=state.users.find(u=>u.id===el.dataset.user);modal(`调整 ${esc(u.display_name)} 的权限`,`<input type="hidden" name="id" value="${esc(u.id)}"><label>角色<select name="role">${Object.entries(roles).map(([k,v])=>`<option value="${k}" ${u.role===k?'selected':''}>${v}</option>`).join('')}</select></label><label>状态<select name="active"><option value="true" ${u.active?'selected':''}>启用</option><option value="false" ${!u.active?'selected':''}>停用</option></select></label><p class="muted">变更后该账号的现有会话将失效。</p>`,'access-form');}
    else if(el.dataset.action)await action(el.dataset.action);
  }catch(error){notice(error.message,true);}
});
document.addEventListener('change',async e=>{if(e.target.id==='state-filter'){state.filter=e.target.value;state.offset=0;try{await load();}catch(error){notice(error.message,true);}}});
document.addEventListener('submit',async e=>{
  e.preventDefault(); if(state.busy)return; state.busy=true;
  const form=e.target, formId=form.getAttribute('id'), button=form.querySelector('button.primary');button.disabled=true;
  const data=Object.fromEntries(new FormData(form));
  try{
    if(formId==='login-form'){
      const result=await post('/api/auth/login',data);state.token=result.access_token;state.user=result.user;state.page='tickets';state.offset=0;state.selected=null;state.users=[];
      if(allowed('users:manage'))state.users=await api('/api/users');
      state.modules=(await api('/api/platform')).modules;render();await load();
    }else{
      if(formId==='create-form'){data.room_id=Number(data.room_id);const t=await post('/api/m3/tickets',data);state.selected=await api(`/api/m3/tickets/${t.id}`);state.offset=0;state.filter='';}
      else if(formId==='user-form')await post('/api/users',data);
      else if(formId==='access-form'){const uid=data.id;delete data.id;data.active=data.active==='true';await api(`/api/users/${uid}/access`,{method:'PUT',body:JSON.stringify(data)});}
      else if(formId==='password-form'){await post('/api/auth/password',data);state.token='';state.user=null;render();}
      else{const operation=formId.replace('-form','');if(operation==='accept')data.approved=data.approved==='true';if(operation==='review')data.rating=Number(data.rating);await post(`/api/m3/tickets/${state.selected.id}/${operation}`,data);}
      $('#modal').close();notice('操作成功');if(state.user)await load();
    }
  }catch(error){notice(error.message,true);}finally{state.busy=false;button.disabled=false;}
});
// 刷新仅用于展示，超时升级和同步重试由服务端后台任务独立执行。
setInterval(()=>{if(state.user&&!state.busy&&!$('#modal').open)load().catch(()=>{});},10000);
render();
