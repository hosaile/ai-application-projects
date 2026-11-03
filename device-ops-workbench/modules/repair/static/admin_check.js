
const $ = id => document.getElementById(id);
const SL = {pending:'待处理', processing:'处理中', done:'已完成', closed:'已关闭'};
const CATS = ['网络','水电','门锁','设备','其他'];

function esc(s){
  return String(s == null ? '' : s).replace(/[&<>"]/g, c =>
    ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
}
function toast(msg, type){
  let wrap = document.getElementById('toastWrap');
  if (!wrap) {
    wrap = document.createElement('div');
    wrap.id = 'toastWrap';
    wrap.className = 'toast-wrap';
    document.body.appendChild(wrap);
  }
  const el = document.createElement('div');
  el.className = 'toast ' + (type || '');
  el.textContent = msg;
  wrap.appendChild(el);
  requestAnimationFrame(() => el.classList.add('show'));
  setTimeout(() => { el.classList.remove('show'); setTimeout(() => el.remove(), 250); }, 2600);
}
async function api(url, opts){
  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    throw new Error('本地服务未响应：请确认软件仍在运行，必要时重新双击打开');
  }
  if (res.status === 401) {
    location.href = '/repair/login?next=' + encodeURIComponent(location.pathname + location.search);
    throw new Error('需要管理员登录');
  }
  let data = {};
  try { data = await res.json(); } catch(e) { data = {error:'响应解析失败'}; }
  if (!res.ok) throw new Error(data.error || ('请求失败 ' + res.status));
  return data;
}

async function refreshHealth(){
  try {
    const h = await api('api/health');
    $('aiState').textContent = h.ai_enabled
      ? 'AI 分诊：已开启（' + h.model + '）'
      : 'AI 分诊：规则模式';
  } catch (e) {}
}
refreshHealth();

let currentNo = null;

let firstLoad = true;

function skeleton(){
  return '<div class="skel"><i></i><i></i></div>'.repeat(3);
}

const EMPTY_ICON = '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" ' +
  'stroke="#c9c5b8" stroke-width="1.2" stroke-linecap="round">' +
  '<path d="M4 5h16v14H4z"/><path d="M8 9h8M8 12.5h5M8 16h3"/></svg>';

async function load(){
  const params = new URLSearchParams();
  if ($('fStatus').value) params.set('status', $('fStatus').value);
  if ($('fCategory').value) params.set('category', $('fCategory').value);
  if ($('fPriority').value) params.set('priority', $('fPriority').value);
  if (firstLoad) {
    $('list').innerHTML = skeleton();
    firstLoad = false;
  }
  try {
    const [list, stats, metrics] = await Promise.all([
      api('api/tickets?' + params.toString()),
      api('api/stats'),
      api('api/metrics')
    ]);
    renderList(list.tickets || []);
    renderStats(stats);
    renderMetrics(metrics);
  } catch (err) {
    $('list').innerHTML = '<div class="empty">' + esc(err.message) + '</div>';
  }
}

function renderStats(s){
  $('sToday').textContent = s.today_new;
  $('sPending').textContent = s.pending;
  $('sAvg').innerHTML = (s.avg_hours == null ? '–' : s.avg_hours) + '<small> 小时</small>';
  const by = s.by_category_7d || {};
  const max = Math.max.apply(null, CATS.map(c => by[c] || 0).concat([1]));
  $('bars').innerHTML = CATS.map(c => {
    const n = by[c] || 0;
    return '<div class="bar-row"><span>' + c + '</span>' +
      '<div class="bar"><i style="width:' + Math.round(n / max * 100) + '%"></i></div>' +
      '<span class="c">' + n + '</span></div>';
  }).join('');
  $('sOverdue').textContent = s.overdue != null ? s.overdue : '–';
}


function renderMetrics(m){
  const pct = v => v == null ? '–' : Math.round(v * 100) + '%';
  $('metrics').innerHTML =
    '<div class="metric"><div class="k">AI 分诊占比</div><div class="v">' + pct(m.ai_rate) + '</div>' +
      '<div class="s">共 ' + (m.last30 || 0) + ' 单</div></div>' +
    '<div class="metric"><div class="k">SLA 按时完成率</div><div class="v">' + pct(m.sla_rate) + '</div>' +
      '<div class="s">已完成工单</div></div>' +
    '<div class="metric"><div class="k">重复报修率</div><div class="v">' + pct(m.repeat_rate) + '</div>' +
      '<div class="s">越低越好</div></div>' +
    '<div class="metric"><div class="k">经验文档</div><div class="v">' + (m.experience_docs || 0) + '</div>' +
      '<div class="s">已沉淀到知识库</div></div>';
}

function renderList(rows){
  if (!rows.length) {
    $('list').innerHTML = '<div class="empty">' + EMPTY_ICON + '暂无工单</div>';
    return;
  }
  $('list').innerHTML = rows.map(t => {
    const conf = Math.round((t.confidence || 0) * 100);
    const lowConf = t.ai_mode === 'ai' && (t.confidence || 0) < 0.75;
    const priCls = t.priority === '高' ? 'pri-high' : t.priority === '低' ? 'pri-low' : 'pri-mid';
    return '<div class="trow ' + priCls + '" data-no="' + esc(t.ticket_no) + '">' +
      '<div class="t-line1">' +
        '<span class="t-no">' + esc(t.ticket_no) + '</span>' +
        '<span class="chip st-' + esc(t.status) + '">' + esc(t.status_label) + '</span>' +
        '<span class="chip ' + (t.priority === '高' ? 'pri-high' : '') + '">' + esc(t.priority) + '优先级</span>' +
        '<span class="chip">' + esc(t.category) + '</span>' +
        (t.overdue ? '<span class="chip pri-high">已超时</span>' : '') +
        (t.sla_breached ? '<span class="chip lowconf">完成超时</span>' : '') +
        (t.ai_mode === 'ai'
          ? '<span class="chip ' + (lowConf ? 'lowconf' : '') + '">AI ' + conf + '%' + (lowConf ? ' · 待确认' : '') + '</span>'
          : '<span class="chip">规则</span>') +
      '</div>' +
      '<div class="t-desc">' + esc(t.description).slice(0, 60) + '</div>' +
      '<div class="t-meta">' + esc(t.location || '未填地点') + ' · ' + esc(t.created_at) +
        (t.assignee ? ' · 维修人 ' + esc(t.assignee) : '') + '</div>' +
    '</div>';
  }).join('');
  Array.prototype.forEach.call(document.querySelectorAll('.trow'), el => {
    el.addEventListener('click', () => openDetail(el.dataset.no));
  });
}

async function openDetail(no){
  currentNo = no;
  try {
    const data = await api('api/tickets/' + encodeURIComponent(no));
    const t = data.ticket, ev = data.events || [];
    const conf = Math.round((t.confidence || 0) * 100);
    const stBtn = (val, label) =>
      '<button data-act="status" data-status="' + val + '" ' +
      (t.status === val ? 'class="on"' : 'class="ghost"') + '>' + label + '</button>';
    $('drawerIn').innerHTML =
      '<div class="d-head"><span class="d-no">' + esc(t.ticket_no) + '</span>' +
        '<button class="x" id="closeDrawer" aria-label="关闭">×</button></div>' +
      '<div class="d-meta">' +
        '<span class="chip st-' + esc(t.status) + '">' + esc(t.status_label) + '</span>' +
        '<span class="chip">' + esc(t.category) + '</span>' +
        '<span class="chip ' + (t.priority === '高' ? 'pri-high' : '') + '">' + esc(t.priority) + '优先级</span>' +
        '<span class="chip">' + (t.ai_mode === 'ai' ? 'AI 分诊 ' + conf + '%' : '规则分诊') + '</span>' +
        (t.total_tokens ? '<span class="chip">' + t.total_tokens + ' tokens · ' + (t.ai_ms || 0) + 'ms</span>' : '') +
      '</div>' +
      '<div class="d-block"><div class="k">问题描述</div>' +
        '<div class="d-desc">' + esc(t.description) + '</div></div>' +
      (data.asset ? '<div class="d-block"><div class="k">关联设备</div>' +
        '<div style="font-size:14px"><a href="/a/' + encodeURIComponent(data.asset.code) + '" ' +
        'style="color:var(--blue)">' + esc(data.asset.name) + '（' + esc(data.asset.code) + '）</a>' +
        ' · ' + esc(data.asset.location || '未填位置') +
        ' · 保修至 ' + esc(data.asset.warranty_until || '未填') + '</div></div>' : '') +
      (t.ai_sources && t.ai_sources.length ? '<div class="d-block"><div class="k">分诊依据（知识库）</div>' +
        '<ul class="checks">' + t.ai_sources.map(s =>
          '<li>《' + esc(s.source) + '》' + esc(s.heading) +
          ' · 相似度 ' + esc(s.score) + '</li>').join('') + '</ul></div>' : '') +
      '<div class="d-block"><div class="k">学生可先自查</div>' +
        '<ul class="checks">' + (t.ai_checks || []).map(c => '<li>' + esc(c) + '</li>').join('') + '</ul></div>' +
      '<div class="d-block"><div class="k">SLA 时限</div>' +
        '<div style="font-size:14px">' + esc(t.sla_due_at || '未设置') +
        (t.overdue ? ' · <b style="color:var(--danger)">已超时</b>' : '') +
        (t.sla_breached ? ' · <b style="color:var(--danger)">完成时已超时</b>' : '') +
        (t.first_response_at ? ' · 首次响应 ' + esc(t.first_response_at) : '') + '</div></div>' +
      '<div class="d-block"><div class="k">报修信息</div>' +
        '<div style="font-size:14px">地点：' + esc(t.location || '未填') +
        ' · 姓名：' + esc(t.reporter || '未填') +
        ' · 联系方式：' + esc(t.contact || '未填') + '</div></div>' +
      '<div class="d-block"><div class="k">状态流转</div>' +
        '<div class="ops">' + stBtn('processing', '开始处理') + stBtn('done', '完成') + stBtn('closed', '关闭') + '</div></div>' +
      '<div class="d-block"><div class="k">人工调整类别（AI 误判时使用）</div>' +
        '<div class="op-row">' +
          '<select id="catSel">' + CATS.map(c =>
            '<option ' + (c === t.category ? 'selected' : '') + '>' + c + '</option>').join('') + '</select>' +
          '<button class="ghost" data-act="cat">保存类别</button>' +
        '</div></div>' +
      '<div class="d-block"><div class="k">指派与备注</div>' +
        '<div class="op-row2">' +
          '<input id="assigneeIn" placeholder="维修人" value="' + esc(t.assignee || '') + '">' +
          '<input id="noteIn" placeholder="备注" value="' + esc(t.note || '') + '">' +
          '<button class="ghost" data-act="save">保存</button>' +
        '</div></div>' +
      ((t.status === 'done' || t.status === 'closed') ?
        '<div class="d-block"><div class="k">知识沉淀</div>' +
        '<div class="ops"><button class="ghost" data-act="kcs">沉淀为经验</button></div></div>' : '') +
      '<div class="d-block"><div class="k">处理记录</div>' +
        '<ul class="timeline">' + ev.map(e =>
          '<li><span class="t">' + esc(e.created_at.slice(5, 16)) + '</span>' +
          esc(SL[e.to_status] || e.to_status || '提交') +
          (e.note ? ' · ' + esc(e.note) : '') + '</li>').join('') + '</ul></div>';
    $('drawer').classList.add('open');
    $('mask').classList.add('open');
  } catch (err) {
    toast(err.message, 'err');
  }
}

function closeDrawer(){
  $('drawer').classList.remove('open');
  $('mask').classList.remove('open');
  currentNo = null;
}

$('mask').addEventListener('click', closeDrawer);
document.addEventListener('click', e => {
  if (e.target && e.target.id === 'closeDrawer') closeDrawer();
});

$('drawer').addEventListener('click', async e => {
  const b = e.target.closest('button[data-act]');
  if (!b || !currentNo) return;
  const no = currentNo;
  try {
    if (b.dataset.act === 'status') {
      await api('api/tickets/' + no, {method:'PATCH', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({status: b.dataset.status, note: b.dataset.status === 'done' ? '维修完成' : ''})});
    } else if (b.dataset.act === 'cat') {
      await api('api/tickets/' + no, {method:'PATCH', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({category: $('catSel').value})});
    } else if (b.dataset.act === 'kcs') {
      openKcs(no);
      return;
    } else if (b.dataset.act === 'save') {
      await api('api/tickets/' + no, {method:'PATCH', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({assignee: $('assigneeIn').value, note: $('noteIn').value})});
    }
    await openDetail(no);
    await load();
  } catch (err) {
    toast(err.message, 'err');
  }
});

$('fStatus').addEventListener('change', load);
$('fCategory').addEventListener('change', load);
$('fPriority').addEventListener('change', load);
$('refreshBtn').addEventListener('click', load);

const _qs = new URLSearchParams(location.search);
const presetNo = _qs.get('ticket') || _qs.get('no');
load().then(() => { if (presetNo) openDetail(presetNo); });
setInterval(() => { if (!currentNo) load(); }, 15000);

// 桌面版心跳（浏览器访问时无副作用）
setInterval(() => { fetch('api/ping').catch(() => {}); }, 5000);

// ---------- 统一模型信息 ----------
function showTest(msg, ok){
  const el = $('testResult');
  el.className = 'test-result ' + (ok === null || ok === undefined ? '' : (ok ? 'test-ok' : 'test-fail'));
  el.textContent = msg || '';
}

async function loadUnifiedModel(){
  $('unifiedModelInfo').textContent = '加载中…';
  try {
    const s = await api('api/settings');
    const state = s.configured ? '已就绪' : '未配置 Key（规则分诊）';
    $('unifiedModelInfo').textContent =
      '供应商：' + (s.provider_name || '未配置') + '\n' +
      '模型：' + (s.model_name || s.model || '未配置') +
        (s.model && s.model_name && s.model_name !== s.model ? '（' + s.model + '）' : '') + '\n' +
      '状态：' + state + '\n' +
      '配置入口：工作台 → 模型设置（问答 / 报修共用）';
  } catch (e) {
    $('unifiedModelInfo').textContent = e.message;
  }
}

$('settingsBtn').addEventListener('click', async () => {
  $('settingsModal').classList.add('open');
  showTest('', null);
  await Promise.all([loadUnifiedModel(), loadDataInfo()]);
});
$('closeSettings').addEventListener('click', () => $('settingsModal').classList.remove('open'));
$('settingsMask').addEventListener('click', () => $('settingsModal').classList.remove('open'));
$('refreshModelBtn').addEventListener('click', () => {
  loadUnifiedModel();
  loadDataInfo();
});

// ---------- 数据与日志 ----------
async function loadDataInfo(){
  try {
    const d = await api('api/data/info');
    const kb = Math.max(1, Math.round(d.db_size / 1024));
    $('dataInfo').textContent =
      '数据库：' + d.db_path + '（' + kb + ' KB）\n' +
      '工单 ' + d.tickets + ' 条 · 处理记录 ' + d.events + ' 条 · 累计 AI 用量 ' + (d.total_tokens || 0) + ' tokens\n' +
      '日志：' + d.log_path + '\n' +
      '启动备份：' + d.backup_path;
  } catch (e) {
    $('dataInfo').textContent = e.message;
  }
}

$('backupBtn').addEventListener('click', () => {
  window.location.href = 'api/data/backup';
});

$('resetDataBtn').addEventListener('click', async () => {
  if (!confirm('将清空当前全部工单，并重建 15 条演示数据。确定继续？')) return;
  try {
    const r = await api('api/data/reset', {method: 'POST'});
    await loadDataInfo();
    await load();
    showTest('已重置为 ' + r.inserted + ' 条演示数据', true);
  } catch (e) { showTest(e.message, false); }
});



// ---------- 工单经验沉淀 ----------
let kcsNo = null;

function kcsMsg(text, ok){
  const el = $('kcsMsg');
  el.className = 'test-result ' + (ok === null || ok === undefined ? '' : (ok ? 'test-ok' : 'test-fail'));
  el.textContent = text || '';
}

async function openKcs(no){
  kcsNo = no;
  $('kcsMeta').textContent = no;
  $('kcsContent').value = '加载中…';
  $('kcsView').style.display = 'none';
  kcsMsg('', null);
  $('kcsModal').classList.add('open');
  try {
    const d = await api('api/tickets/' + encodeURIComponent(no) + '/experience');
    $('kcsContent').value = d.content || '';
    $('kcsView').style.display = d.exists ? '' : 'none';
    $('kcsView').href = '/#kb';
  } catch (e) {
    $('kcsContent').value = '';
    kcsMsg(e.message, false);
  }
}
function closeKcs(){ $('kcsModal').classList.remove('open'); }
$('kcsClose').addEventListener('click', closeKcs);
$('kcsMask').addEventListener('click', closeKcs);

async function saveKcs(rebuild){
  if (!kcsNo) return;
  const btn = rebuild ? $('kcsSaveRebuild') : $('kcsSave');
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = rebuild ? '保存并重建中…' : '保存中…';
  kcsMsg('', null);
  try {
    const d = await api('api/tickets/' + encodeURIComponent(kcsNo) + '/experience', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({content: $('kcsContent').value, rebuild: !!rebuild}),
    });
    $('kcsView').style.display = '';
    if (d.warning) kcsMsg(d.warning, false);
    else if (d.rebuilt) kcsMsg('已保存并重建索引（' + d.chunks + ' 块），问答/分诊现在可以检索到它。', true);
    else kcsMsg('已保存到 ' + d.path + '；如需立即可检索，请点「保存并重建索引」。', true);
    loadDataInfo();
  } catch (e) {
    kcsMsg(e.message, false);
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}
$('kcsSave').addEventListener('click', () => saveKcs(false));
$('kcsSaveRebuild').addEventListener('click', () => saveKcs(true));

// ---------- 设备资产 ----------
let editingAssetId = null;
let assetCache = {};

function assetMsg(text, ok){
  const el = $('assetMsg');
  el.className = 'test-result ' + (ok === null || ok === undefined ? '' : (ok ? 'test-ok' : 'test-fail'));
  el.textContent = text || '';
}

function renderAssets(items){
  assetCache = {};
  items.forEach(a => { assetCache[a.id] = a; });
  if (!items.length) {
    $('assetList').innerHTML = '<div class="empty">暂无设备。先录入关键设备（AP / 交换机 / 门禁 / 投影），' +
      '后续报修可自动关联并生成二维码。</div>';
    return;
  }
  $('assetList').innerHTML = items.map(a =>
    '<div class="trow" style="cursor:default">' +
      '<div class="t-line1"><span class="t-no">' + esc(a.code) + '</span>' +
        '<span class="chip">' + esc(a.category || '其他') + '</span>' +
        '<span class="chip">' + esc(a.status || '在用') + '</span></div>' +
      '<div class="t-desc">' + esc(a.name) + '</div>' +
      '<div class="t-meta">' + esc(a.location || '未填位置') + ' · ' +
        esc(a.owner || '未指定负责人') +
        (a.warranty_until ? ' · 保修至 ' + esc(a.warranty_until) : '') + '</div>' +
      '<div class="ops">' +
        '<button class="ghost" data-act="open" data-id="' + a.id + '">档案</button>' +
        '<button class="ghost" data-act="qr" data-id="' + a.id + '">二维码</button>' +
        '<button class="ghost" data-act="edit" data-id="' + a.id + '">编辑</button>' +
      '</div>' +
    '</div>').join('');
}

async function loadAssets(){
  const q = $('assetSearch').value.trim();
  try {
    const d = await api('api/assets?q=' + encodeURIComponent(q));
    renderAssets(d.assets || []);
  } catch (e) {
    $('assetList').innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
  }
}

function openAsset(a){
  editingAssetId = a ? a.id : null;
  $('assetModalTitle').textContent = a ? '编辑设备' : '新增设备';
  const vals = a || {};
  $('asCode').value = vals.code || '';
  $('asName').value = vals.name || '';
  $('asCategory').value = vals.category || '网络设备';
  $('asLocation').value = vals.location || '';
  $('asOwner').value = vals.owner || '';
  $('asStatus').value = vals.status || '在用';
  $('asWarranty').value = vals.warranty_until || '';
  $('asVendor').value = vals.vendor || '';
  $('asManual').value = vals.manual || '';
  $('asNote').value = vals.note || '';
  $('assetDeleteBtn').style.display = a ? '' : 'none';
  assetMsg('', null);
  $('assetModal').classList.add('open');
}
function closeAsset(){ $('assetModal').classList.remove('open'); }

$('assetAddBtn').addEventListener('click', () => openAsset(null));
$('assetClose').addEventListener('click', closeAsset);
$('assetMask').addEventListener('click', closeAsset);


$('assetImportBtn').addEventListener('click', () => $('assetFile').click());
$('assetFile').addEventListener('change', async () => {
  const file = $('assetFile').files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append('file', file);
  try {
    const r = await api('api/assets/import', {method: 'POST', body: fd});
    await loadAssets();
    toast('导入完成：新增 ' + r.inserted + ' · 更新 ' + r.updated +
      (r.skipped.length ? ' · 跳过 ' + r.skipped.length : ''), 'ok');
  } catch (e) {
    toast(e.message, 'err');
  } finally {
    $('assetFile').value = '';
  }
});

$('assetSearch').addEventListener('input', () => {
  clearTimeout(window.__assetTimer);
  window.__assetTimer = setTimeout(loadAssets, 200);
});

$('assetList').addEventListener('click', e => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const a = assetCache[+btn.dataset.id];
  if (!a) return;
  if (btn.dataset.act === 'open') location.href = '/a/' + encodeURIComponent(a.code);
  else if (btn.dataset.act === 'qr') window.open('api/assets/' + a.id + '/qr.png', '_blank');
  else if (btn.dataset.act === 'edit') openAsset(a);
});

$('assetSaveBtn').addEventListener('click', async () => {
  const payload = {
    code: $('asCode').value.trim(),
    name: $('asName').value.trim(),
    category: $('asCategory').value,
    location: $('asLocation').value.trim(),
    owner: $('asOwner').value.trim(),
    status: $('asStatus').value,
    warranty_until: $('asWarranty').value.trim(),
    vendor: $('asVendor').value.trim(),
    manual: $('asManual').value.trim(),
    note: $('asNote').value.trim(),
  };
  if (!payload.code || !payload.name) {
    assetMsg('设备编号和名称必填', false);
    return;
  }
  $('assetSaveBtn').disabled = true;
  try {
    await api('api/assets' + (editingAssetId ? '/' + editingAssetId : ''), {
      method: editingAssetId ? 'PATCH' : 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
    });
    closeAsset();
    await loadAssets();
    toast(editingAssetId ? '设备已更新' : '设备已新增', 'ok');
  } catch (e) {
    assetMsg(e.message, false);
  } finally {
    $('assetSaveBtn').disabled = false;
  }
});

$('assetDeleteBtn').addEventListener('click', async () => {
  if (!editingAssetId) return;
  if (!confirm('确定删除这台设备？已有工单不会被删除，但会失去设备关联。')) return;
  try {
    await api('api/assets/' + editingAssetId, {method: 'DELETE'});
    closeAsset();
    await loadAssets();
    toast('设备已删除', 'ok');
  } catch (e) { assetMsg(e.message, false); }
});


// ---------- 演示模式 ----------
async function loadDemoStatus(){
  try {
    const s = await api('api/demo/status');
    $('demoBanner').classList.toggle('show', !!s.active);
    $('demoBtn').textContent = s.active ? '退出演示' : '演示模式';
  } catch (e) {}
}
async function toggleDemo(){
  try {
    const s = await api('api/demo/status');
    const r = await api(s.active ? 'api/demo/exit' : 'api/demo/enter', {method: 'POST'});
    await loadDemoStatus();
    await load();
    await loadAssets();
    toast(s.active ? '已退出演示模式' : '已进入演示模式（独立演示数据）', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}
$('demoBtn').addEventListener('click', toggleDemo);
$('demoExitBtn').addEventListener('click', toggleDemo);
loadDemoStatus();

loadAssets();
if (location.hash === '#assets') {
  setTimeout(() => {
    const el = document.getElementById('assets');
    if (el) el.scrollIntoView({behavior: 'smooth'});
  }, 300);
}

// 深链接：/admin?settings=1 直接打开设置
if (new URLSearchParams(location.search).get('settings')) {
  $('settingsModal').classList.add('open');
  loadUnifiedModel();
  loadDataInfo();
}
