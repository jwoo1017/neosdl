import Graph from "graphology";
import dagre from "dagre";

import moduleIcon from "./icons/module.svg?url";
import classIcon from "./icons/class.svg?url";
import interfaceIcon from "./icons/interface.svg?url";
import functionIcon from "./icons/function.svg?url";
import methodIcon from "./icons/method.svg?url";
import fieldIcon from "./icons/field.svg?url";
import externalIcon from "./icons/external.svg?url";

export const KIND_COLORS = {
  module: "#94a3b8",
  class: "#3b82f6",
  interface: "#8b5cf6",
  function: "#22c55e",
  method: "#14b8a6",
  field: "#eab308",
  external: "#cbd5e1",
};

// 종류별 아이콘 — 전부 frontend/src/icons/ 아래 한 곳에서 관리한다.
export const KIND_ICONS = {
  module: moduleIcon,
  class: classIcon,
  interface: interfaceIcon,
  function: functionIcon,
  method: methodIcon,
  field: fieldIcon,
  external: externalIcon,
};

// @sigma/node-image 의 NodeImageProgram 을 이 타입 이름으로 등록해서 쓴다
// (GraphView.jsx 의 SigmaContainer settings.nodeProgramClasses 참고).
export const ICON_NODE_TYPE = "icon";

// ColorBrewer "YlOrRd" 4-class 순차형 팔레트(축약) — 옅은 노랑→주황→진한 빨강.
// 변경 빈도 같은 단조 증가 값을 색으로 인코딩할 때 가장 널리 쓰이는
// 계열이라(GIS/BI 대시보드 표준), 임의 그라디언트 대신 이걸 그대로 쓴다.
const HEAT_STOPS = [
  [254, 240, 217], // #fef0d9
  [253, 204, 138], // #fdcc8a
  [252, 141, 89],  // #fc8d59
  [215, 48, 39],   // #d7301f
];
const HEAT_NEUTRAL = "#e2e8f0";
const PATH_EDGE_COLOR = "#3fb8ac"; // 흐름 경로 엣지 강조(테마 accent) // 변경 이력이 0인 노드는 눈에 띄지 않는 중립 회색

// 코드 핫스팟 뷰(변경 빈도 히트맵) 노드 크기 범위.
const HOTSPOT_MIN_SIZE = 7;
const HOTSPOT_MAX_SIZE = 26;

/** t(0~1)를 HEAT_STOPS 구간에서 선형 보간한 rgb() 문자열로. */
export function heatColor(t) {
  const clamped = Math.max(0, Math.min(1, t));
  const segments = HEAT_STOPS.length - 1;
  const pos = clamped * segments;
  const i = Math.min(Math.floor(pos), segments - 1);
  const frac = pos - i;
  const [r1, g1, b1] = HEAT_STOPS[i];
  const [r2, g2, b2] = HEAT_STOPS[i + 1];
  const r = Math.round(r1 + (r2 - r1) * frac);
  const g = Math.round(g1 + (g2 - g1) * frac);
  const b = Math.round(b1 + (b2 - b1) * frac);
  return `rgb(${r}, ${g}, ${b})`;
}

/**
 * value를 노드 반지름으로 바꾼다. 값에 반지름을 선형 비례시키지 않고
 * sqrt(value)에 비례시킨다 — 원의 "면적"이 값에 비례해야 사람 눈에 크기
 * 차이가 정확히 인지된다(Flannery 보정, D3/Tableau 등 표준 라이브러리의
 * 기본 규칙). 반지름을 값에 직접 비례시키면 큰 값이 실제보다 과장돼 보인다.
 * hotspot 뷰(변경 빈도)와 domain 뷰(파일 수) 둘 다 이 공식을 그대로 쓴다.
 */
function proportionalSize(value, maxValue, minSize = HOTSPOT_MIN_SIZE, maxSize = HOTSPOT_MAX_SIZE) {
  if (!maxValue || value <= 0) return minSize;
  const t = Math.sqrt(value / maxValue);
  return minSize + (maxSize - minSize) * t;
}

// 도메인 그래프 노드용 categorical 팔레트 — 도메인 수만큼 순환해서 배정한다.
const DOMAIN_PALETTE = [
  "#3b82f6", "#22c55e", "#f97316", "#8b5cf6", "#14b8a6",
  "#eab308", "#ec4899", "#06b6d4", "#84cc16", "#f43f5e",
];

/**
 * 도메인을 노드로 하는 롤업 그래프. `nodes`는 {id, label, file_count}, `edges`는
 * {id, source, target, weight}(둘 다 GET /scans/{id}/graph/domains 응답 형태).
 * 노드 크기는 파일 수에 비례(면적 비례, 위 proportionalSize 재사용), 엣지
 * 두께는 가중치에 로그 스케일 비례 — 가중치 분포가 넓을 수 있어(1~수백)
 * 선형이면 작은 값들이 전부 안 보이게 뭉개진다.
 */
export function buildDomainGraph(nodes, edges) {
  const dg = new dagre.graphlib.Graph();
  dg.setGraph({ rankdir: "TB", nodesep: 60, ranksep: 90 });
  dg.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) dg.setNode(n.data.id, { width: 40, height: 40 });
  for (const e of edges) {
    if (dg.hasNode(e.data.source) && dg.hasNode(e.data.target)) {
      dg.setEdge(e.data.source, e.data.target);
    }
  }
  dagre.layout(dg);

  const maxFileCount = Math.max(0, ...nodes.map((n) => n.data.file_count || 0));
  const maxWeight = Math.max(1, ...edges.map((e) => e.data.weight || 0));

  const graph = new Graph({ type: "directed" });
  nodes.forEach((n, i) => {
    const pos = dg.node(n.data.id) || { x: 0, y: 0 };
    graph.addNode(n.data.id, {
      x: pos.x,
      y: pos.y,
      size: proportionalSize(n.data.file_count, maxFileCount, 10, 34),
      label: `${n.data.label} (${n.data.file_count})`,
      color: DOMAIN_PALETTE[i % DOMAIN_PALETTE.length],
      type: "circle",
    });
  });
  for (const e of edges) {
    if (!graph.hasNode(e.data.source) || !graph.hasNode(e.data.target)) continue;
    const t = Math.log(1 + e.data.weight) / Math.log(1 + maxWeight);
    graph.mergeEdge(e.data.source, e.data.target, {
      size: 0.6 + t * 5,
      color: "#94a3b8",
      weight: e.data.weight,
      type: "arrow",
    });
  }
  return graph;
}

// CFG/PDG/CPG 뷰의 레이어별 엣지 색 — cfg(구조)는 중립 회색, data(PDG 데이터
// 의존)는 파랑, control(PDG 제어 의존)은 주황. 같은 그래프 위에 여러 종류의
// edge를 얹는 CPG 개념을 색으로 구분해서 보여준다.
const LAYER_COLORS = { cfg: "#94a3b8", data: "#3b82f6", control: "#f97316" };
const BLOCK_COLOR = "#64748b";
const ENTRY_BLOCK_COLOR = "#22c55e";

/** `src.order.service.cancelOrder` → `cancelOrder` — 그래프 라벨은 짧아야 겹치지 않는다. */
export function shortLabel(name) {
  const s = String(name || "");
  const parts = s.split(/::|#|\./);
  return parts[parts.length - 1] || s;
}

// 코드 지도 모듈(커뮤니티) 색 — '기타'(-1)는 중립 회색.
export function communityColor(id) {
  if (id === null || id === undefined || id < 0) return "#94a3b8";
  return DOMAIN_PALETTE[id % DOMAIN_PALETTE.length];
}

/**
 * 결정적 force-directed 레이아웃(Fruchterman–Reingold). dagre TB 는 호출 그래프를
 * 한 줄로 길게 늘어뜨려 라벨이 겹친다 — 모듈 지도/모듈 내부처럼 계층이 없는
 * 그래프는 연결된 것끼리 뭉치는 배치가 읽기 쉽다. 시드는 원형 배치라 매번 같은
 * 모양이 나온다. weights: 노드별 질량(클수록 서로 더 밀어냄).
 */
export function forceLayout(ids, pairs, { iterations = 250, weights = null } = {}) {
  const n = ids.length;
  const pos = new Map();
  if (n === 0) return pos;
  const area = 1000 * 1000;
  const k = Math.sqrt(area / n);
  ids.forEach((id, i) => {
    const a = (2 * Math.PI * i) / n;
    const r = 300 + (i % 7) * 20;
    pos.set(id, { x: Math.cos(a) * r, y: Math.sin(a) * r });
  });
  const mass = (id) => (weights ? weights.get(id) || 1 : 1);
  const links = pairs.filter(([a, b]) => a !== b && pos.has(a) && pos.has(b));
  let temp = 120;
  for (let it = 0; it < iterations; it++) {
    const disp = new Map(ids.map((id) => [id, { x: 0, y: 0 }]));
    for (let i = 0; i < n; i++) {
      const pi = pos.get(ids[i]);
      for (let j = i + 1; j < n; j++) {
        const pj = pos.get(ids[j]);
        let dx = pi.x - pj.x;
        let dy = pi.y - pj.y;
        const d = Math.max(0.01, Math.hypot(dx, dy));
        const f = ((k * k) / d) * Math.sqrt(mass(ids[i]) * mass(ids[j]));
        dx = (dx / d) * f;
        dy = (dy / d) * f;
        const di = disp.get(ids[i]);
        const dj = disp.get(ids[j]);
        di.x += dx; di.y += dy;
        dj.x -= dx; dj.y -= dy;
      }
    }
    for (const [a, b] of links) {
      const pa = pos.get(a);
      const pb = pos.get(b);
      const dx = pa.x - pb.x;
      const dy = pa.y - pb.y;
      const d = Math.max(0.01, Math.hypot(dx, dy));
      const f = (d * d) / k;
      const da = disp.get(a);
      const db = disp.get(b);
      da.x -= (dx / d) * f; da.y -= (dy / d) * f;
      db.x += (dx / d) * f; db.y += (dy / d) * f;
    }
    for (const id of ids) {
      const p = pos.get(id);
      const d = disp.get(id);
      const len = Math.max(0.01, Math.hypot(d.x, d.y));
      // 약한 중심 인력 — 연결 없는 덩어리가 화면 밖으로 날아가지 않게
      p.x += (d.x / len) * Math.min(len, temp) - p.x * 0.01;
      p.y += (d.y / len) * Math.min(len, temp) - p.y * 0.01;
    }
    temp = Math.max(2, temp * 0.97);
  }
  return pos;
}

/**
 * 코드 지도 첫 화면 — 모듈(graphify 커뮤니티)을 노드로, 모듈 간 호출 수를 엣지로.
 * communities/links 는 GET /scans/{id}/code-map 응답 그대로.
 */
export function buildCommunityMapGraph(communities, links) {
  const ids = communities.map((c) => String(c.id));
  const maxSize = Math.max(1, ...communities.map((c) => c.size));
  const weights = new Map(communities.map((c) => [String(c.id), 1 + Math.sqrt(c.size / maxSize) * 2]));
  const pos = forceLayout(ids, links.map((l) => [String(l.source), String(l.target)]), { weights });
  const maxWeight = Math.max(1, ...links.map((l) => l.weight));

  const graph = new Graph({ type: "directed" });
  for (const c of communities) {
    const p = pos.get(String(c.id)) || { x: 0, y: 0 };
    graph.addNode(String(c.id), {
      x: p.x,
      y: p.y,
      size: proportionalSize(c.size, maxSize, 9, 34),
      label: `${c.name} (${c.size})`,
      color: communityColor(c.id),
      type: "circle",
      communityId: c.id,
      forceLabel: true,
    });
  }
  for (const l of links) {
    const s = String(l.source);
    const t = String(l.target);
    if (!graph.hasNode(s) || !graph.hasNode(t)) continue;
    const w = Math.log(1 + l.weight) / Math.log(1 + maxWeight);
    graph.mergeEdge(s, t, { size: 0.6 + w * 5, color: "#94a3b8", weight: l.weight, type: "arrow" });
  }
  return graph;
}

/**
 * 함수 하나의 CFG/PDG/CPG. `GET /scans/{id}/function-graph`가 내려주는
 * 블록 노드 + layer가 붙은 엣지를 그대로 받아, `layers`에 포함된 것만 그린다
 * (CFG=["cfg"], PDG=["data","control"], CPG=["cfg","data","control"]).
 */
export function buildFunctionGraph(nodes, edges, layers) {
  const dg = new dagre.graphlib.Graph();
  dg.setGraph({ rankdir: "TB", nodesep: 50, ranksep: 70 });
  dg.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) dg.setNode(n.data.id, { width: 30, height: 30 });
  const kept = edges.filter((e) => layers.includes(e.data.layer));
  for (const e of kept) {
    if (dg.hasNode(e.data.source) && dg.hasNode(e.data.target)) {
      dg.setEdge(e.data.source, e.data.target);
    }
  }
  dagre.layout(dg);

  const graph = new Graph({ type: "directed" });
  for (const n of nodes) {
    const pos = dg.node(n.data.id) || { x: 0, y: 0 };
    graph.addNode(n.data.id, {
      x: pos.x,
      y: pos.y,
      size: n.data.is_entry ? 14 : 10,
      label: n.data.is_entry ? `${n.data.label} (Entry)` : n.data.label,
      color: n.data.is_entry ? ENTRY_BLOCK_COLOR : BLOCK_COLOR,
      type: "circle",
      startLine: n.data.start_line,
    });
  }
  for (const e of kept) {
    if (!graph.hasNode(e.data.source) || !graph.hasNode(e.data.target)) continue;
    graph.mergeEdge(e.data.source, e.data.target, {
      size: e.data.layer === "cfg" ? 1.2 : 1.6,
      color: LAYER_COLORS[e.data.layer] || "#94a3b8",
      layer: e.data.layer,
      type: "arrow",
      // 엣지 클릭 시 근거 패널에 쓴다 — data면 var/evidence_kind, cfg/control이면 branch_label.
      var: e.data.var,
      evidenceKind: e.data.evidence_kind,
      branchLabel: e.data.branch_label,
    });
  }
  return graph;
}

/**
 * 포커스 노드 기준 N-hop 이내로 노드/엣지를 걸러낸다(BFS, 방향 무시 —
 * 호출하는 쪽/받는 쪽 둘 다 봐야 "이 노드 주변"이라는 말이 성립한다).
 *
 * hairball 문제의 진짜 원인은 dagre가 스캔 전체(수백~천 개 노드)를 항상
 * 레이아웃하는 것이다 — 클릭 후 안 이웃을 흐리게 하는 기존 기능은 레이아웃
 * 자체를 줄이지 않아서 문제를 안 고친다. 이 함수는 레이아웃 계산 *전에*
 * 대상을 줄여서 dagre가 애초에 작은 그래프만 배치하게 만든다.
 */
export function filterByFocus(nodes, edges, focusId, depth) {
  const adjacency = new Map();
  const addEdge = (a, b) => {
    if (!adjacency.has(a)) adjacency.set(a, new Set());
    adjacency.get(a).add(b);
  };
  for (const e of edges) {
    addEdge(e.data.source, e.data.target);
    addEdge(e.data.target, e.data.source);
  }

  const keep = new Set([focusId]);
  let frontier = [focusId];
  for (let d = 0; d < depth; d++) {
    const next = [];
    for (const id of frontier) {
      for (const neighbor of adjacency.get(id) || []) {
        if (!keep.has(neighbor)) {
          keep.add(neighbor);
          next.push(neighbor);
        }
      }
    }
    frontier = next;
  }

  return {
    nodes: nodes.filter((n) => keep.has(n.data.id)),
    edges: edges.filter((e) => keep.has(e.data.source) && keep.has(e.data.target)),
  };
}

/**
 * API의 {nodes, edges}(Cytoscape elements 형태)를 graphology 그래프로 바꾼다.
 * 렌더링은 Sigma(WebGL)가 하지만, 위→아래 트리 모양은 dagre로 좌표만
 * 계산해서 그대로 주입한다 — sigma/react-sigma 생태계엔 계층형 레이아웃이
 * 없어서, 레이아웃 계산과 렌더링을 분리하는 통상적인 방식을 쓴다.
 *
 * `options.hotspotMode`가 켜지면 노드 크기/색을 kind가 아니라 최근 변경
 * 빈도(recent_changes)로 인코딩하는 "코드 핫스팟" 뷰로 그린다(아이콘 대신
 * 단색 원 — 아이콘은 불투명 이미지라 색을 못 씌운다).
 */
export function buildGraphologyGraph(nodes, edges, options = {}) {
  // layout: "force"(모듈 내부 — 연결된 것끼리 뭉침) | "dagre"(전체/포커스 — 위→아래 호출 계층)
  //         | "sequence"(흐름 경로 — options.order 순서대로 왼쪽→오른쪽)
  // colorBy: "kind"(아이콘) | "community"(코드 지도 모듈 색 원)
  // emphasizeSeeds: data.seed 노드(근거)를 크게+라벨 고정, 나머지는 작게
  // pathEdges: 심볼 키 순서 — 연속한 쌍의 엣지를 강조색으로
  const {
    hotspotMode = false, maxRecentChanges = 0, layout = "dagre", colorBy = "kind",
    order = null, emphasizeSeeds = false, pathEdges = null,
  } = options;
  let positionOf;
  if (layout === "sequence") {
    const index = new Map((order || []).map((k, i) => [k, i]));
    let extra = 0;
    // 지그재그로 살짝 엇갈려 아래 라벨끼리 겹치지 않게
    positionOf = (id) => (index.has(id)
      ? { x: index.get(id) * 170, y: (index.get(id) % 2) * 46 }
      : { x: (extra++) * 170, y: 180 });
  } else if (layout === "force") {
    const pos = forceLayout(nodes.map((n) => n.data.id), edges.map((e) => [e.data.source, e.data.target]));
    positionOf = (id) => pos.get(id);
  } else {
    const dg = new dagre.graphlib.Graph();
    dg.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 70 });
    dg.setDefaultEdgeLabel(() => ({}));
    for (const n of nodes) {
      dg.setNode(n.data.id, { width: 30, height: 30 });
    }
    for (const e of edges) {
      if (dg.hasNode(e.data.source) && dg.hasNode(e.data.target)) {
        dg.setEdge(e.data.source, e.data.target);
      }
    }
    dagre.layout(dg);
    positionOf = (id) => dg.node(id);
  }

  // multi:true 를 줘도 무의미하다 — @react-sigma/core 의 useLoadGraph 는
  // 이 그래프를 Sigma 내부의 (non-multi) 그래프로 import 하므로 거기서
  // 결국 충돌한다. 같은 (source,target) 쌍은 mergeEdge 로 합친다 — 같은
  // 대상을 여러 줄에서 부르는 미해석 호출이 실제로 이런 중복을 만든다.
  const graph = new Graph({ type: "directed" });
  for (const n of nodes) {
    const pos = positionOf(n.data.id) || { x: 0, y: 0 };
    const recentChanges = n.data.recent_changes || 0;
    const label = shortLabel(n.data.label);

    if (hotspotMode) {
      graph.addNode(n.data.id, {
        x: pos.x,
        y: pos.y,
        size: proportionalSize(recentChanges, maxRecentChanges),
        label,
        fullLabel: n.data.label,
        color: recentChanges > 0 ? heatColor(recentChanges / (maxRecentChanges || 1)) : HEAT_NEUTRAL,
        kind: n.data.kind,
        recentChanges,
        type: "circle",
        path: n.data.path,
        startLine: n.data.start_line,
      });
    } else if (colorBy === "community") {
      // 모듈 바깥 이웃(boundary)은 작고 흐리게 — 이 모듈이 누구와 이어지는지만 보여준다.
      const seed = !!n.data.seed;
      graph.addNode(n.data.id, {
        x: pos.x,
        y: pos.y,
        size: n.data.boundary ? 5 : emphasizeSeeds ? (seed ? 13 : 6) : 9,
        label,
        fullLabel: n.data.label,
        forceLabel: emphasizeSeeds && seed,
        seed,
        color: n.data.boundary ? "#cbd5e1" : communityColor(n.data.community),
        kind: n.data.kind,
        recentChanges,
        type: "circle",
        path: n.data.path,
        startLine: n.data.start_line,
        boundary: !!n.data.boundary,
      });
    } else {
      graph.addNode(n.data.id, {
        x: pos.x,
        y: pos.y,
        size: n.data.kind === "external" ? 8 : 11,
        label,
        fullLabel: n.data.label,
        color: KIND_COLORS[n.data.kind] || "#94a3b8",
        kind: n.data.kind,
        recentChanges,
        type: ICON_NODE_TYPE,
        image: KIND_ICONS[n.data.kind] || KIND_ICONS.external,
        path: n.data.path,
        startLine: n.data.start_line,
      });
    }
  }
  // 경로에서 연속한 (a → b) 쌍 — 이 엣지들만 강조색으로.
  const pathNext = new Map();
  for (let i = 0; pathEdges && i < pathEdges.length - 1; i++) {
    if (!pathNext.has(pathEdges[i])) pathNext.set(pathEdges[i], new Set());
    pathNext.get(pathEdges[i]).add(pathEdges[i + 1]);
  }
  for (const e of edges) {
    if (!graph.hasNode(e.data.source) || !graph.hasNode(e.data.target)) continue;
    const onPath = !!pathNext.get(e.data.source)?.has(e.data.target);
    graph.mergeEdge(e.data.source, e.data.target, {
      size: onPath ? 3 : e.data.resolved ? 1.2 : 0.8,
      color: onPath ? PATH_EDGE_COLOR : e.data.resolved ? "#94a3b8" : "#e2e8f0",
      resolved: e.data.resolved,
      type: "arrow",
    });
  }
  // 오염 경로 hop 은 호출 엣지가 아니라 인자→파라미터 전달일 수 있다 — 그래도 순서가 보이게 잇는다.
  for (let i = 0; pathEdges && i < pathEdges.length - 1; i++) {
    const [a, b] = [pathEdges[i], pathEdges[i + 1]];
    if (a !== b && graph.hasNode(a) && graph.hasNode(b) && !graph.hasEdge(a, b)) {
      graph.addEdge(a, b, { size: 2, color: PATH_EDGE_COLOR, type: "arrow", synthetic: true });
    }
  }
  return graph;
}

/**
 * Sigma 기본 라벨은 노드 오른쪽에 그린다 (`sigma`의 drawDiscNodeLabel 구현:
 * `fillText(label, data.x + data.size + 3, ...)`). 노드 안은 이제 아이콘이
 * 채우므로, 라벨은 노드 바로 아래 중앙 정렬로 그린다 (참고 이미지 스타일).
 * `SigmaContainer settings={{ defaultDrawNodeLabel: drawNodeLabelBelow }}`로 등록.
 */
export function drawNodeLabelBelow(context, data, settings) {
  if (!data.label) return;

  const size = settings.labelSize;
  const font = settings.labelFont;
  const weight = settings.labelWeight;
  context.font = `${weight} ${size}px ${font}`;

  const maxWidth = 150;
  let label = data.label;
  let width = context.measureText(label).width;
  if (width > maxWidth) {
    while (width > maxWidth && label.length > 1) {
      label = label.slice(0, -1);
      width = context.measureText(`${label}…`).width;
    }
    label = `${label}…`;
  }

  // Sigma 캔버스 자체는 컨테이너 CSS 배경(다크 테마)과 무관하게 흰 배경으로
  // 그려진다 — 그 위에 검은 글씨를 유지한다.
  // 흰 외곽선(halo)을 먼저 그려 엣지/다른 노드 위에서도 글자가 깨져 보이지 않게 한다.
  context.textAlign = "center";
  context.textBaseline = "top";
  context.lineWidth = 3;
  context.lineJoin = "round";
  context.strokeStyle = "rgba(255, 255, 255, 0.9)";
  context.strokeText(label, data.x, data.y + data.size + 4);
  context.fillStyle = "#0f172a";
  context.fillText(label, data.x, data.y + data.size + 4);
}
