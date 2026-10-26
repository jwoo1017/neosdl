// 화면 전체 한→영 번역 엔진 — Chrome 내장 Translator API(기기 안에서 번역, 외부 전송 없음)를 쓴다.
// React와 무관하게 DOM 텍스트 노드의 nodeValue만 바꾼다(노드를 갈아끼우지 않아 React 재조정이 깨지지 않음).
// MutationObserver로 페이지 이동·새 메시지·스트리밍 답변도 따라가며, revert()로 원문을 되돌린다.

const SOURCE = "ko";
const TARGET = "en";
const HANGUL = /[가-힣ㄱ-ㅎㅏ-ㅣ]/;
const ATTRS = ["placeholder", "title", "aria-label"];
// 코드·사용자 입력·번역 금지 영역은 건드리지 않는다
const SKIP_SELECTOR = [
  "script", "style", "noscript", "pre", "code", "kbd", "samp", "textarea",
  "[translate='no']", ".notranslate", "[contenteditable='']", "[contenteditable='true']",
].join(",");
const MAX_CONCURRENT = 4;
const DEBOUNCE_MS = 400;

const OPTIONS = { sourceLanguage: SOURCE, targetLanguage: TARGET };

export function isSupported() {
  return typeof self !== "undefined" && "Translator" in self;
}

// "unavailable" | "downloadable" | "downloading" | "available"
export async function availability() {
  if (!isSupported()) return "unavailable";
  try {
    return await self.Translator.availability(OPTIONS);
  } catch {
    return "unavailable";
  }
}

let translator = null;
let observer = null;
let active = false;
const cache = new Map(); // 원문 → Promise<번역문>
const textState = new WeakMap(); // Text → { orig, translated }
const attrState = new WeakMap(); // Element → { [attr]: { orig, translated } }
const touched = new Set(); // 되돌리기 대상(Text 또는 Element)
const pending = new Map(); // 디바운스 중인 노드 → timer
let running = 0;
const queue = [];
let onBusyChange = null;

// 모델을 내려받아야 하면 사용자 클릭 안에서 호출해야 한다.
let creating = null;
export async function ensureTranslator(onProgress) {
  if (translator) return translator;
  creating ??= self.Translator.create({
    ...OPTIONS,
    monitor(m) {
      m.addEventListener("downloadprogress", (e) => onProgress?.(e.loaded));
    },
  });
  try {
    translator = await creating;
  } finally {
    creating = null;
  }
  return translator;
}

function setBusy() {
  onBusyChange?.(running > 0 || queue.length > 0);
}

function runQueued() {
  while (running < MAX_CONCURRENT && queue.length > 0) {
    const { text, resolve, reject } = queue.shift();
    running += 1;
    translator.translate(text).then(resolve, reject).finally(() => {
      running -= 1;
      runQueued();
      setBusy();
    });
  }
  setBusy();
}

function translateText(text) {
  // 앞뒤 공백은 보존하고 가운데만 번역한다(인라인 요소 사이 띄어쓰기 유지)
  const m = text.match(/^(\s*)([\s\S]*?)(\s*)$/);
  const [, lead, core, trail] = m;
  let p = cache.get(core);
  if (!p) {
    p = new Promise((resolve, reject) => {
      queue.push({ text: core, resolve, reject });
      runQueued();
    });
    p.catch(() => cache.delete(core));
    cache.set(core, p);
  }
  return p.then((out) => lead + out + trail);
}

function skipped(el) {
  return !el || el.closest(SKIP_SELECTOR) !== null;
}

function translateTextNode(node) {
  const value = node.nodeValue;
  if (!value || !HANGUL.test(value)) return;
  const prev = textState.get(node);
  if (prev && prev.translated === value) return; // 이미 우리가 넣은 번역문
  if (skipped(node.parentElement)) return;
  translateText(value).then((out) => {
    // 번역하는 사이 React가 값을 바꿨거나 꺼졌다면 버린다
    if (!active || node.nodeValue !== value) return;
    textState.set(node, { orig: value, translated: out });
    touched.add(node);
    node.nodeValue = out;
  }, () => {});
}

function translateAttrs(el) {
  if (skipped(el)) return;
  for (const attr of ATTRS) {
    const value = el.getAttribute(attr);
    if (!value || !HANGUL.test(value)) continue;
    const prev = attrState.get(el)?.[attr];
    if (prev && prev.translated === value) continue;
    translateText(value).then((out) => {
      if (!active || el.getAttribute(attr) !== value) return;
      const state = attrState.get(el) || {};
      state[attr] = { orig: value, translated: out };
      attrState.set(el, state);
      touched.add(el);
      el.setAttribute(attr, out);
    }, () => {});
  }
}

function scan(root) {
  if (root.nodeType === Node.TEXT_NODE) {
    translateTextNode(root);
    return;
  }
  if (root.nodeType !== Node.ELEMENT_NODE) return;
  if (skipped(root)) return;
  translateAttrs(root);
  for (const el of root.querySelectorAll(ATTRS.map((a) => `[${a}]`).join(","))) translateAttrs(el);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => (HANGUL.test(n.nodeValue) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP),
  });
  for (let n = walker.nextNode(); n; n = walker.nextNode()) translateTextNode(n);
}

// 스트리밍 답변처럼 같은 노드가 계속 바뀌면 멈췄을 때 한 번만 번역한다
function schedule(node) {
  clearTimeout(pending.get(node));
  pending.set(node, setTimeout(() => {
    pending.delete(node);
    if (active && node.isConnected) scan(node);
  }, DEBOUNCE_MS));
}

function onMutations(records) {
  for (const r of records) {
    if (r.type === "childList") {
      r.addedNodes.forEach(schedule);
    } else if (r.type === "characterData") {
      const prev = textState.get(r.target);
      if (prev && prev.translated === r.target.nodeValue) continue;
      schedule(r.target);
    } else if (r.type === "attributes") {
      const prev = attrState.get(r.target)?.[r.attributeName];
      if (prev && prev.translated === r.target.getAttribute(r.attributeName)) continue;
      schedule(r.target);
    }
  }
}

// translator가 준비된 뒤 호출한다. onBusy(bool)로 번역 진행 여부를 알린다.
export function start(onBusy) {
  onBusyChange = onBusy;
  if (active) return;
  active = true;
  document.documentElement.lang = TARGET;
  observer = new MutationObserver(onMutations);
  observer.observe(document.body, {
    childList: true, subtree: true, characterData: true,
    attributes: true, attributeFilter: ATTRS,
  });
  scan(document.body);
}

export function revert() {
  active = false;
  observer?.disconnect();
  observer = null;
  for (const t of pending.values()) clearTimeout(t);
  pending.clear();
  queue.length = 0;
  for (const node of touched) {
    if (node.nodeType === Node.TEXT_NODE) {
      const s = textState.get(node);
      // React가 이미 다른 값으로 바꿨다면 그 값이 최신 원문이므로 두고 간다
      if (s && node.nodeValue === s.translated) node.nodeValue = s.orig;
      textState.delete(node);
    } else {
      const s = attrState.get(node) || {};
      for (const [attr, { orig, translated }] of Object.entries(s)) {
        if (node.getAttribute(attr) === translated) node.setAttribute(attr, orig);
      }
      attrState.delete(node);
    }
  }
  touched.clear();
  document.documentElement.lang = SOURCE;
  onBusyChange?.(false);
}
