"use strict";

const tabs = [...document.querySelectorAll('[role="tab"]')];

function selectTab(selected) {
  for (const tab of tabs) {
    const active = tab === selected;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    const panel = document.getElementById(tab.getAttribute("aria-controls"));
    panel.hidden = !active;
    const frame = panel.querySelector("iframe");
    if (active && frame.dataset.src) {
      frame.src = frame.dataset.src;
      delete frame.dataset.src;
    }
  }
}

tabs.forEach((tab, index) => {
  const panel = document.getElementById(tab.getAttribute("aria-controls"));
  const frame = panel.querySelector("iframe");
  const markEmbedded = () => frame.contentDocument?.body?.classList.add("monitor-embedded");
  frame.addEventListener("load", markEmbedded);
  markEmbedded();
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", event => {
    const destinations = {ArrowLeft: (index + tabs.length - 1) % tabs.length,
      ArrowRight: (index + 1) % tabs.length, Home: 0, End: tabs.length - 1};
    if (!Object.hasOwn(destinations, event.key)) return;
    event.preventDefault();
    const next = tabs[destinations[event.key]];
    selectTab(next);
    next.focus();
  });
});
