"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const navigation = require("../research-web/assets/research-navigation.js");
const source = fs.readFileSync(path.join(__dirname, "../research-web/assets/research-navigation.js"), "utf8");

test("public navigation initializes only for the existing Research page", () => {
  assert.equal(navigation.initialize(null), false);
  assert.match(source, /root\.querySelectorAll\("\[data-tab\]"\)/);
  assert.match(source, /\.scrh-view\[data-view\]/);
  assert.match(source, /data-tab-target/);
  assert.match(source, /scrh-discover-form/);
  assert.match(source, /scrh-graph-svg-wrap/);
  assert.doesNotMatch(source, /credentials:.*include|localStorage|sessionStorage|innerHTML|eval\(/);
  assert.doesNotMatch(source, /fetch\(|XMLHttpRequest|\/private\/projects/);
});

test("existing editor-approved public records are the only local discovery input", () => {
  const row = {
    querySelectorAll: () => [
      {textContent: "", querySelector: (q) => q === ".scrh-record-title" ?
        {textContent: "Example Site rock weathering"} : {textContent: "Basalt field study"}},
      {textContent: "Climate, land & carbon"}, {textContent: "Research active"}, {textContent: "Scientific investigation"}
    ]
  };
  const root = {querySelectorAll: () => [row]};
  const result = navigation.readPreviewRows(root);
  assert.equal(result.length, 1);
  assert.equal(result[0].title, "Example Site rock weathering");
  assert.equal(result[0].area, "Climate, land & carbon");
  assert.strictEqual(result[0].row, row);
});

test("tab and sidebar controls change the visible panel and ARIA state", () => {
  function element(data) {
    const events = {};
    return {
      dataset: data, hidden: data.view !== "overview", tabIndex: -1,
      attrs: {},
      setAttribute(k,v) {this.attrs[k]=v;},
      addEventListener(k,fn) {events[k]=fn;},
      click() {events.click?.({});},
      querySelector() {return {tabIndex:-1,focus(){}};},
      classList: {toggle() {}}
    };
  }
  const names = ["overview","discover","areas","graph","updates"];
  const tabs = names.map(n => element({tab:n}));
  const panels = names.map(n => element({view:n}));
  const index = {};
  panels.forEach((p,i) => index["#scrh-view-"+names[i]] = p);
  index["#scrh-home-articles"] = {};
  const root = {
    dataset:{},
    querySelector(sel) { return index[sel] || null; },
    querySelectorAll(sel) {
      if (sel === "[data-tab]") return tabs;
      if (sel === ".scrh-view[data-view]") return panels;
      return [];
    }
  };
  assert.equal(navigation.initialize(root), true);
  assert.equal(root.dataset.scNavigationReady,"yes");
  assert.equal(navigation.initialize(root), false);
  tabs[1].click();
  assert.equal(panels[1].hidden,false);
  assert.equal(panels[0].hidden,true);
  assert.equal(tabs[1].attrs["aria-selected"],"true");
  tabs[3].click();
  assert.equal(panels[3].hidden,false);
  assert.equal(panels[1].hidden,true);
});
