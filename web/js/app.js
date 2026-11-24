import {api} from './api.js';
import {catalogView} from './catalog.js';
import {renderControls} from './controls.js';
import {renderCurves} from './curves.js';
import {bindDelivery} from './delivery.js';
import {byId, detailList, el} from './dom.js';
import {bindIntake} from './intake.js';
import {renderSequence} from './sequence.js';
import {bindTabs} from './tabs.js';
import {renderTrace} from './trace.js';

let current = null;
let injection = null;
let runRequest = 0;
let traceRequest = 0;
const filter = byId('kind-filter');
const trace = byId('injection-trace');
const setTab = bindTabs();

function notify(message, problem = false) {
  const board = byId('notification');
  board.textContent = message;
  board.dataset.error = String(problem);
  board.hidden = false;
}

const catalog = catalogView(open, error => notify(error.message, true));
const delivery = bindDelivery(() => current, async id => {
  await catalog.load();
  if (current?.id === id) await open(id, true);
}, notify);

function updateSequence() {
  renderSequence(byId('injection-list'), current.injections, filter.value, injection, selectInjection);
}

async function selectInjection(id) {
  const token = ++traceRequest;
  injection = id;
  updateSequence();
  if (!id) {
    trace.replaceChildren(el('p', '请选择一次进样。', 'detail'));
    return;
  }
  trace.replaceChildren(el('p', '读取进样证据…', 'detail'));
  try {
    const evidence = await api.trace(current.id, id);
    if (token === traceRequest) renderTrace(trace, evidence, current.method.targets);
  } catch (error) {
    if (token === traceRequest) trace.replaceChildren(el('p', error.message, 'problem'));
  }
}

async function open(id, keepView = false) {
  const token = ++runRequest;
  try {
    const report = await api.open(id);
    if (token !== runRequest) return;
    current = report;
    catalog.select(id);
    byId('empty').hidden = true;
    byId('work').hidden = false;
    byId('batch-id').textContent = report.id;
    byId('batch-title').textContent = report.title;
    byId('batch-method').textContent = `方法 ${report.method.id} / ${report.method.revision} · ${report.injections.length} 次进样`;
    byId('batch-summary').replaceChildren(detailList([['可交付组分', report.counts.reportable], ['暂缓组分', report.counts.held]]));
    renderCurves(byId('curves'), report);
    renderControls(byId('controls'), report, id => {
      filter.value = 'all';
      setTab('sequence');
      selectInjection(id);
    });
    delivery(report, keepView);
    if (!keepView) {
      filter.value = 'all';
      setTab('sequence');
    }
    const first = report.injections.find(row => row.kind === 'sample') ?? report.injections[0];
    await selectInjection(keepView ? injection : first?.id);
  } catch (error) {
    if (token === runRequest) notify(error.message, true);
  }
}

async function imported(result) {
  await catalog.load();
  await open(result.id);
  notify(result.reused ? '批次已存在，打开原有记录。' : '批次已导入并完成核算。');
}

bindIntake(imported);
filter.addEventListener('change', () => {
  if (!current) return;
  const matches = current.injections.filter(row => filter.value === 'all' || row.kind === filter.value);
  if (matches.some(row => row.id === injection)) updateSequence();
  else selectInjection(matches[0]?.id);
});
byId('load-example').addEventListener('click', async event => {
  const action = event.currentTarget;
  action.disabled = true;
  try {
    await imported(await api.ingest(await api.example()));
  } catch (error) {
    notify(error.message, true);
  } finally {
    action.disabled = false;
  }
});
catalog.load();
