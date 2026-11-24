import {api} from './api.js';
import {byId, el, table} from './dom.js';
import {number, word} from './terms.js';

const CONCLUSION = {
  interchangeable: '可确认互换',
  not_interchangeable: '可确认不可换',
  insufficient_evidence: '证据不足',
  pairing_insufficient: '配对资料不足',
};
const verdictClass = {
  interchangeable: 'good', not_interchangeable: 'caution', insufficient_evidence: 'caution',
  pairing_insufficient: 'caution',
};

const conclusion = status => el('span', CONCLUSION[status] ?? status, `badge ${verdictClass[status] ?? 'caution'}`);
const value = entry => entry === null || entry === undefined ? '—' : number(entry);

function pairLabel(pair) {
  return `${pair.left} ↔ ${pair.right}`;
}

function sideStatus(pair) {
  const side = (status, issues) => {
    const reasons = issues?.length ? `（${issues.map(word).join('、')}）` : '';
    return `${word(status)}${reasons}`;
  };
  return `${side(pair.left_status, pair.left_issues)} / ${side(pair.right_status, pair.right_issues)}`;
}

function pairDetail(pair) {
  if (pair.held) {
    return `暂缓，超差未知`;
  }
  return `差 ${number(pair.difference)} · ${pair.exceeded ? '超差' : '合格'}`;
}

function pairingSection(interchange) {
  const wrap = el('div', null, 'register-block');
  const complete = interchange.pairing.complete;
  wrap.append(el('h4', '配对审核'));
  if (!complete) {
    wrap.append(el('p', '整张清单没有完整配对，按配对资料不足单列。', 'problem'));
  }
  const rows = interchange.pairing.mother_samples.map(mother => {
    const relations = mother.relations.map(relation =>
      `${pairLabel(relation)}（${relation.source}）`).join('；') || '无';
    const state = mother.complete ? '完整配对' : (mother.reason === 'count_mismatch' ? '两侧针数不等' : '无完整配对');
    return [mother.id, mother.source, mother.left.join('、'), mother.right.join('、'), relations, state];
  });
  wrap.append(table(['母样', '出处', '左侧进样', '右侧进样', '允许对应（出处）', '状态'], rows));
  for (const mother of interchange.pairing.mother_samples) {
    if (!mother.matchings?.length) continue;
    const listed = mother.matchings.map((matching, index) => [
      `第 ${index + 1} 种`, matching.map(pairLabel).join('；')]);
    wrap.append(el('p', `母样 ${mother.id} 的可行对应（${mother.matchings.length}${mother.matchings_truncated ? '+' : ''} 种）`, 'detail'));
    wrap.append(table(['序号', '对应'], listed));
  }
  return wrap;
}

function targetSection(target) {
  const block = el('div', null, 'register-block');
  const head = el('div', null, 'section-head');
  head.append(el('h4', `${target.label} · ${target.id}`), conclusion(target.conclusion));
  const facts = el('dl', null, 'facts');
  const add = (name, datum) => {
    const group = el('div');
    group.append(el('dt', name), el('dd', datum instanceof Node ? datum : String(datum)));
    facts.append(group);
  };
  add('差限（mg/L）', number(target.limit));
  add('容许超差对数', target.allowed_exceedances);
  add('超差对数范围', target.min === null ? '配对资料不足' : `${target.min} 到 ${target.max}`);
  block.append(head, facts);
  if (target.minimum && target.maximum) {
    for (const [label, witness] of [['最少超差对应', target.minimum], ['最多超差对应', target.maximum]]) {
      const rows = witness.pairs.map(pair => [pairLabel(pair), value(pair.left_value), value(pair.right_value),
        sideStatus(pair), pairDetail(pair), pair.source]);
      block.append(el('p', `${label}（${witness.count} 对超差）`, 'detail'));
      block.append(table(['对应', '左侧浓度', '右侧浓度', '两侧判定', '本对判定', '出处'], rows));
    }
  }
  return block;
}

export function bindRegisters(current, notify) {
  const form = byId('register-form');
  const toggle = byId('toggle-register-form');
  const selectForm = byId('register-select-form');
  const choice = byId('register-choice');
  const output = byId('register-result');
  let cached = [];

  toggle.addEventListener('click', () => {
    form.hidden = !form.hidden;
    toggle.textContent = form.hidden ? '登记新清单' : '收起登记';
  });

  function options(items) {
    const fragment = document.createDocumentFragment();
    for (const item of items) {
      for (const version of item.versions) {
        const option = el('option', `${item.register_id} · 第 ${version.version} 版${version.supersedes ? '（续）' : ''}`);
        option.value = JSON.stringify([item.register_id, version.version, item.left_run, item.right_run]);
        fragment.append(option);
      }
    }
    if (!fragment.childElementCount) {
      const option = el('option', '尚无分样清单');
      option.value = '';
      fragment.append(option);
    }
    choice.replaceChildren(fragment);
  }

  async function refresh(report) {
    try {
      const listed = await api.registers(report.id);
      if (current()?.id !== report.id) return;
      cached = listed.items;
      options(cached);
    } catch (error) {
      notify(error.message, true);
    }
  }

  function renderInterchange(payload) {
    const comparison = payload.comparison;
    const container = el('div');
    const head = el('div', null, 'section-head');
    head.append(el('h3', `${payload.id} · 第 ${payload.version} 版`),
      el('span', comparison.interchange.pairing.complete ? '整张清单完整配对' : '配对资料不足', 'detail'));
    container.append(head);
    container.append(el('p', `${comparison.batches.left} ↔ ${comparison.batches.right} · 摘要 ${payload.hash}` +
      (payload.supersedes ? ` · 取代 ${payload.supersedes}` : ''), 'digest'));
    container.append(pairingSection(comparison.interchange));
    const targets = Object.values(comparison.interchange.targets).sort((a, b) => a.id.localeCompare(b.id));
    for (const target of targets) container.append(targetSection(target));
    output.replaceChildren(container);
  }

  form.addEventListener('submit', async event => {
    event.preventDefault();
    const report = current();
    if (!report) return;
    let parsed;
    try {
      parsed = JSON.parse(byId('register-json').value);
    } catch {
      notify('分样清单须为合法 JSON。', true);
      return;
    }
    try {
      const saved = await api.saveRegister(report.id, parsed);
      notify(`已登记 ${saved.id} 第 ${saved.version} 版。`);
      form.hidden = true;
      toggle.textContent = '登记新清单';
      await refresh(report);
    } catch (error) {
      notify(error.message, true);
    }
  });

  selectForm.addEventListener('submit', async event => {
    event.preventDefault();
    const report = current();
    if (!report) return;
    let selected = null;
    try {
      selected = JSON.parse(choice.value);
    } catch {
      selected = null;
    }
    if (!selected) {
      notify('请先登记分样清单。', true);
      return;
    }
    const [register, version] = selected;
    output.replaceChildren(el('p', '判定中…', 'detail'));
    try {
      const payload = await api.registerDetail(report.id, register, version);
      if (current()?.id !== report.id) return;
      renderInterchange(payload);
    } catch (error) {
      if (current()?.id === report.id) output.replaceChildren(el('p', error.message, 'problem'));
    }
  });

  return async report => {
    output.replaceChildren();
    await refresh(report);
  };
}
