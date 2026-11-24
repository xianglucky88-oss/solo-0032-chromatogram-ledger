export const byId = id => document.getElementById(id);

export function el(tag, text, className) {
  const item = document.createElement(tag);
  if (text !== undefined && text !== null) item.textContent = text;
  if (className) item.className = className;
  return item;
}

export function button(text, callback, className) {
  const item = el('button', text, className);
  item.type = 'button';
  item.addEventListener('click', callback);
  return item;
}

export function detailList(pairs) {
  const list = el('dl', null, 'facts');
  for (const [label, value] of pairs) {
    const group = el('div');
    group.append(el('dt', label), el('dd', value));
    list.append(group);
  }
  return list;
}

export function table(headers, rows) {
  const wrap = el('div', null, 'table-wrap');
  const grid = el('table');
  const head = grid.createTHead().insertRow();
  for (const name of headers) {
    const title = el('th', name);
    title.scope = 'col';
    head.append(title);
  }
  const body = grid.createTBody();
  for (const values of rows) {
    const row = body.insertRow();
    for (const value of values) {
      const cell = row.insertCell();
      if (value instanceof Node) cell.append(value);
      else cell.textContent = value ?? '—';
    }
  }
  wrap.append(grid);
  return wrap;
}
