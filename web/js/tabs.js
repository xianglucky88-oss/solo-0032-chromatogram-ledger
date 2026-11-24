import {byId} from './dom.js';

export function bindTabs() {
  const tabs = [...document.querySelectorAll('[role=tab]')];
  function activate(tab) {
    for (const entry of tabs) {
      const current = entry === tab;
      entry.setAttribute('aria-selected', String(current));
      entry.tabIndex = current ? 0 : -1;
      byId(entry.dataset.panel).hidden = !current;
    }
  }
  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener('click', () => activate(tab));
    tab.addEventListener('keydown', event => {
      const destination = {
        ArrowLeft: (index + tabs.length - 1) % tabs.length,
        ArrowRight: (index + 1) % tabs.length,
        Home: 0,
        End: tabs.length - 1,
      }[event.key];
      if (destination === undefined) return;
      event.preventDefault();
      activate(tabs[destination]);
      tabs[destination].focus();
    });
  }
  return id => activate(tabs.find(tab => tab.dataset.panel === id));
}
