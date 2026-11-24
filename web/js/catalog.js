import {api} from './api.js';
import {byId, button, el} from './dom.js';

export function catalogView(onSelect, onError) {
  const target = byId('catalog');
  const more = byId('more-runs');
  let items = [];
  let offset = null;
  let current = null;

  function render() {
    const fragment = document.createDocumentFragment();
    for (const item of items) {
      const tile = button('', () => onSelect(item.id), 'run-tile');
      if (item.id === current) tile.setAttribute('aria-current', 'true');
      tile.append(el('span', item.id, 'identifier'), el('strong', item.title),
        el('small', `${item.counts.reportable} 可交付 · ${item.counts.held} 暂缓${item.snapshot_hash ? ' · 已封存' : ''}`));
      fragment.append(tile);
    }
    if (!items.length) fragment.append(el('p', '尚未导入批次。', 'detail catalog-empty'));
    target.replaceChildren(fragment);
    more.hidden = offset === null;
  }

  async function load(next = 0) {
    more.disabled = true;
    try {
      const page = await api.catalog(next);
      const merged = next ? [...items, ...page.items] : page.items;
      items = [...new Map(merged.map(item => [item.id, item])).values()];
      offset = page.next_offset;
      render();
    } catch (error) {
      onError(error);
    } finally {
      more.disabled = false;
    }
  }

  more.addEventListener('click', () => load(offset));
  return {load, select(id) { current = id; render(); }};
}
