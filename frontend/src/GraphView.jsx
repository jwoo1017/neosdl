import { useEffect, useRef, useState } from "react";
import {
  SigmaContainer,
  ControlsContainer,
  ZoomControl,
  useLoadGraph,
  useRegisterEvents,
  useCamera,
  useSetSettings,
  useSigma,
} from "@react-sigma/core";
import { MiniMap } from "@react-sigma/minimap";
import { NodeImageProgram } from "@sigma/node-image";
import "@react-sigma/core/lib/style.css";
import AgentActivityTimeline from "./AgentActivityTimeline";
import CodeViewer from "./CodeViewer";
import {
  fetchCodeMap,
  fetchDataflow,
  fetchDomainGraph,
  fetchDomains,
  fetchSubgraph,
  fetchFunctionGraph,
  fetchFunctions,
  fetchGraph,
  searchSymbols,
  streamAskQuestion,
} from "./api";
import {
  buildCommunityMapGraph,
  buildDomainGraph,
  communityColor,
  buildFunctionGraph,
  buildGraphologyGraph,
  drawNodeLabelBelow,
  filterByFocus,
  heatColor,
  ICON_NODE_TYPE,
} from "./buildGraph";
import { TOOL_LABELS } from "./toolLabels";

const NODE_PROGRAM_CLASSES = { [ICON_NODE_TYPE]: NodeImageProgram };

const VIEW_OPTIONS = [
  { value: "map", label: "모듈 지도" },
  { value: "all", label: "전체 심볼 그래프" },
  { value: "cfg", label: "Control Flow Graph" },
  { value: "pdg", label: "Program Dependence Graph" },
  { value: "cpg", label: "Code Property Graph" },
  { value: "domains", label: "Domain Graph" },
];

// 뷰별로 어떤 엣지 layer를 보여줄지 — CPG는 "하나의 그래프에 여러 종류의
// edge를 얹는다"는 개념 그대로 셋 다 겹쳐서 보여준다.
const VIEW_LAYERS = { cfg: ["cfg"], pdg: ["data", "control"], cpg: ["cfg", "data", "control"] };

// 엣지 클릭 시 evidence_kind를 사람이 읽을 문장으로 — "왜 이 근거로
// 연결됐는가"에 대한 최소한의 답.
const EVIDENCE_EXPLANATIONS = {
  DIRECT_ASSIGNMENT: "직접 대입 — 확정된 관계입니다.",
  ALIAS_ASSIGNMENT: "다른 변수를 그대로 옮겨 담은 별칭 대입입니다.",
  FIELD_ALIAS: "객체 필드 접근/대입 — 정밀 추적 대신 보수적으로 이어붙인 관계입니다.",
  UNKNOWN_CALL: "분석 대상 밖의 함수를 거쳐 추정된 관계입니다(내부 동작 미확인).",
  PARAMETER: "호출 인자가 이 파라미터로 전달된 것입니다.",
  DIRECT_ARGUMENT: "변수에 담지 않고 호출 인자로 바로 넘긴 값입니다.",
};

// 경로에서 연속된 같은 함수는 하나로 묶어서 "함수 A → 함수 B" 형태로 보여준다.
function collapsePath(path) {
  const collapsed = [];
  for (const hop of path) {
    const last = collapsed[collapsed.length - 1];
    if (last && last.symbol_key === hop.symbol_key) continue;
    collapsed.push(hop);
  }
  return collapsed;
}

// 답변 근거에서 심볼 키를 모은다 — 서버 _collect_evidence_symbols(app/chat.py)와 같은 순서
// (의미 검색 적중 → 오염 경로 → 도메인 경계 호출 → 호출 그래프), 서브그래프 시드용이라 더 넉넉히.
const MAX_EVIDENCE_SEEDS = 30;
function evidenceSymbolKeys(evidence) {
  const keys = [];
  const add = (k) => {
    if (typeof k === "string" && k && !keys.includes(k)) keys.push(k);
  };
  for (const r of evidence?.semantic_search || []) add(r.symbol_key);
  for (const f of evidence?.dataflow_security || []) {
    add(f.source_symbol_key);
    for (const h of f.path || []) add(h.symbol_key);
    add(f.sink_symbol_key);
  }
  for (const f of evidence?.dataflow_business || []) {
    add(f.caller_symbol_key);
    add(f.callee_symbol_key);
  }
  for (const p of evidence?.call_graph || []) {
    add(p.symbol_key);
    for (const h of p.chain_hops || []) add(h.symbol_key);
  }
  return keys.slice(0, MAX_EVIDENCE_SEEDS);
}

// 서브그래프가 뜬 직후 시드(근거) 노드를 몇 번 부풀렸다 줄여 "여기"를 알려준다.
function SeedPulse({ graph }) {
  const sigma = useSigma();
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return undefined;
    const g = sigma.getGraph();
    const seeds = g.filterNodes((_, a) => a.seed);
    const base = new Map(seeds.map((n) => [n, g.getNodeAttribute(n, "size")]));
    const DURATION = 2400;
    let raf = 0;
    let start = null;
    const tick = (now) => {
      if (start === null) start = now;
      const t = now - start;
      const k = t >= DURATION ? 0 : Math.sin((t / DURATION) * Math.PI * 6) * (1 - t / DURATION);
      for (const n of seeds) {
        if (g.hasNode(n)) g.setNodeAttribute(n, "size", base.get(n) * (1 + 0.45 * Math.max(0, k)));
      }
      if (t < DURATION) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      for (const n of seeds) if (g.hasNode(n)) g.setNodeAttribute(n, "size", base.get(n));
    };
  }, [graph, sigma]);
  return null;
}

function GraphLoader({ graph }) {
  const loadGraph = useLoadGraph();

  useEffect(() => {
    if (graph) loadGraph(graph);
  }, [graph, loadGraph]);

  return null;
}

function GraphInteractions({ externalFocus, onEdgeClick, onNodeClick }) {
  const sigma = useSigma();
  const registerEvents = useRegisterEvents();
  const { gotoNode } = useCamera();
  const setSettings = useSetSettings();
  const [focusNode, setFocusNode] = useState(null);

  useEffect(() => {
    registerEvents({
      clickNode: ({ node }) => {
        setFocusNode(node);
        gotoNode(node, { duration: 300 });
        if (onNodeClick) {
          const g = sigma.getGraph();
          onNodeClick(g.getNodeAttributes(node));
        }
      },
      clickStage: () => setFocusNode(null),
      clickEdge: ({ edge }) => {
        if (!onEdgeClick) return;
        const g = sigma.getGraph();
        const [s, t] = g.extremities(edge);
        onEdgeClick({
          ...g.getEdgeAttributes(edge),
          sourceLabel: g.getNodeAttribute(s, "label"),
          targetLabel: g.getNodeAttribute(t, "label"),
        });
      },
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [registerEvents, gotoNode, onEdgeClick, onNodeClick]);

  // 시맨틱 검색 결과 클릭도 노드를 직접 클릭한 것과 같은 경로를 타게 한다.
  useEffect(() => {
    if (!externalFocus) return;
    const g = sigma.getGraph();
    if (!g.hasNode(externalFocus.nodeId)) return;
    setFocusNode(externalFocus.nodeId);
    gotoNode(externalFocus.nodeId, { duration: 300 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [externalFocus]);

  useEffect(() => {
    const g = sigma.getGraph();
    const neighbors = focusNode && g.hasNode(focusNode)
      ? new Set([focusNode, ...g.neighbors(focusNode)])
      : null;

    setSettings({
      // 흐리게 만들 노드는 아이콘 프로그램 대신 기본 원(circle) 타입으로
      // 바꿔서 흐린 회색 점으로 보이게 한다 — 아이콘은 불투명이라 색만
      // 바꿔선 흐려진 느낌이 안 남.
      nodeReducer: (node, data) => {
        if (!neighbors) return data;
        return neighbors.has(node) ? data : { ...data, type: "circle", color: "#cbd5e1", label: "" };
      },
      edgeReducer: (edge, data) => {
        if (!neighbors) return data;
        const [s, t] = g.extremities(edge);
        return neighbors.has(s) && neighbors.has(t) ? data : { ...data, hidden: true };
      },
    });
  }, [focusNode, sigma, setSettings]);

  return null;
}

// initialHighlight: { keys, mode: "path"|"evidence", title } — 흐름 탭의 "코드 지도에서 보기" 등에서
// 특정 심볼만 강조한 서브그래프로 바로 연다.
export default function GraphView({ scanId, initialHighlight = null }) {
  const [rawGraph, setRawGraph] = useState(null); // { nodes, edges } — 서버 응답 그대로
  const [graph, setGraph] = useState(null); // graphology 인스턴스 — hotspotMode 토글 시 서버 재조회 없이 재조립
  const [status, setStatus] = useState("idle");
  const [stats, setStats] = useState(null);
  const [includeUnresolved, setIncludeUnresolved] = useState(false);
  const [domains, setDomains] = useState([]);
  const [selectedDomain, setSelectedDomain] = useState("");
  const [hotspotMode, setHotspotMode] = useState(false);
  const [maxRecentChanges, setMaxRecentChanges] = useState(0);
  const [view, setView] = useState("map");
  const [codeMap, setCodeMap] = useState(null); // GET /code-map — 모듈 목록 + 요약 리포트
  const [selectedCommunity, setSelectedCommunity] = useState(null); // 모듈 지도에서 드릴다운한 모듈 id
  const [highlight, setHighlight] = useState(initialHighlight);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState(null);
  const [searching, setSearching] = useState(false);
  const [focusRequest, setFocusRequest] = useState(null);
  const [functions, setFunctions] = useState([]);
  const [functionFilter, setFunctionFilter] = useState("");
  const [selectedFunction, setSelectedFunction] = useState("");
  const [functionMeta, setFunctionMeta] = useState(null);
  const [findings, setFindings] = useState(null);
  const [findingsMode, setFindingsMode] = useState("security");
  const [findingsLoading, setFindingsLoading] = useState(false);
  const [edgeInfo, setEdgeInfo] = useState(null);
  const [focusSymbol, setFocusSymbol] = useState("");
  const [focusFilter, setFocusFilter] = useState("");
  const [focusDepth, setFocusDepth] = useState(2);
  const [codeView, setCodeView] = useState(null); // { path, line } | null
  const [askOpen, setAskOpen] = useState(false);
  const [askInput, setAskInput] = useState("");
  const [askEvents, setAskEvents] = useState([]); // AgentEvent[] — 진행 상황 타임라인
  const [askAnswerText, setAskAnswerText] = useState(""); // 스트리밍 중인 답변 토큰 누적
  const [askFinal, setAskFinal] = useState(null); // { answer, tools_used, evidence } | null
  const [asking, setAsking] = useState(false);
  const askAbortRef = useRef(null);

  useEffect(() => () => askAbortRef.current?.abort(), []);

  const isDomainView = view === "domains";
  const isFunctionView = view === "cfg" || view === "pdg" || view === "cpg";
  const isMapView = view === "map";
  // highlight: 근거/경로 심볼만 담은 서브그래프 모드(graphify query 방식) — 다른 뷰보다 우선한다.
  const isHighlight = !!highlight;
  const isMapOverview = isMapView && selectedCommunity === null && !isHighlight;
  // 응답 모양이 뷰마다 달라서(모듈 지도 = 커뮤니티 목록), 뷰 전환 직후 이전 응답으로
  // 엉뚱한 빌더가 돌지 않게 어떤 종류의 데이터인지 표시해 둔다.
  const dataKind = isHighlight ? "highlight" : isMapOverview ? "map" : "graph";
  const currentCommunity = isMapView && codeMap
    ? codeMap.communities.find((c) => c.id === selectedCommunity) || null
    : null;

  useEffect(() => {
    if (!scanId) return;
    setSelectedDomain("");
    fetchDomains(scanId)
      .then((rows) => {
        // role 은 드롭다운에선 안 보여준다 — 같은 domain 여러 role 을 파일수로 합산.
        const totals = new Map();
        for (const r of rows) {
          totals.set(r.domain, (totals.get(r.domain) || 0) + r.file_count);
        }
        setDomains(
          [...totals.entries()]
            .sort((a, b) => b[1] - a[1])
            .map(([domain, fileCount]) => ({ domain, fileCount }))
        );
      })
      .catch(() => setDomains([])); // symbol.annotate 가 아직 안 돌았으면 빈 배열 — 정상
  }, [scanId]);

  // CFG/PDG/CPG의 함수 선택기용 목록 — 스캔당 한 번만 불러와 클라이언트에서 필터링.
  useEffect(() => {
    if (!scanId) return;
    fetchFunctions(scanId)
      .then(setFunctions)
      .catch(() => setFunctions([]));
  }, [scanId]);

  // 스캔이 바뀌면 골라둔 함수는 무효화한다 — view 는 의도적으로 뺐다:
  // CFG<->PDG<->CPG 전환은 같은 함수를 유지해야 하고, 위험 탐지 패널에서
  // jumpToFunction()으로 view+selectedFunction 을 같이 바꿀 때도 view 변경
  // 때문에 방금 지정한 selectedFunction 이 곧바로 리셋되면 안 된다.
  useEffect(() => {
    setSelectedFunction("");
    setFunctionFilter("");
    setEdgeInfo(null);
    setFocusSymbol("");
    setFocusFilter("");
    setCodeView(null);
    setCodeMap(null);
    setSelectedCommunity(null);
  }, [scanId]);

  // 같은 탭에 다른 경로 강조 요청이 다시 오면(흐름 탭에서 또 누름) 교체한다.
  const initialHighlightKey = initialHighlight ? `${initialHighlight.mode}:${initialHighlight.keys.join("|")}` : "";
  useEffect(() => {
    if (initialHighlight) setHighlight(initialHighlight);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanId, initialHighlightKey]);

  useEffect(() => {
    setEdgeInfo(null);
  }, [selectedFunction, view]);

  useEffect(() => {
    if (!scanId) return;
    if (isFunctionView && !selectedFunction && !isHighlight) {
      setRawGraph(null);
      setFunctionMeta(null);
      setStatus("idle");
      return;
    }
    let cancelled = false;
    setStatus("loading");
    setSearchResults(null);

    const load = isHighlight
      ? fetchSubgraph(scanId, highlight.keys, highlight.mode === "path" ? 0 : 1)
      : isMapOverview
      ? fetchCodeMap(scanId).then((cm) => {
          setCodeMap(cm);
          return { nodes: cm.communities, edges: cm.links, max_recent_changes: 0 };
        })
      : isMapView
      ? fetchGraph(scanId, false, "", "all", selectedCommunity)
      : isDomainView
      ? fetchDomainGraph(scanId).then(({ nodes, edges }) => ({ nodes, edges, max_recent_changes: 0 }))
      : isFunctionView
      ? fetchFunctionGraph(scanId, selectedFunction).then(({ nodes, edges, qualified_name, path }) => {
          setFunctionMeta({ qualified_name, path });
          return { nodes, edges, max_recent_changes: 0 };
        })
      : fetchGraph(scanId, includeUnresolved, selectedDomain, view);

    load
      .then(({ nodes, edges, max_recent_changes }) => {
        if (cancelled) return;
        setStats({ nodes: nodes.length, edges: edges.length });
        setMaxRecentChanges(max_recent_changes || 0);
        setRawGraph({ nodes, edges, kind: dataKind });
        setStatus("ready");
      })
      .catch((err) => {
        if (!cancelled) {
          console.error(err);
          setStatus("error");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [scanId, includeUnresolved, selectedDomain, view, isDomainView, isFunctionView, selectedFunction,
      isMapView, isMapOverview, selectedCommunity, isHighlight, highlight, dataKind]);

  // hotspotMode 토글은 이미 받아온 데이터를 다시 조립만 하면 되므로 서버를
  // 다시 부르지 않는다 — 구조 뷰 <-> 핫스팟 뷰 전환이 즉시 반영된다.
  useEffect(() => {
    if (!rawGraph) {
      setGraph(null);
      return;
    }
    if (rawGraph.kind !== dataKind) return; // 새 뷰 데이터가 아직 도착 전
    if (isHighlight) {
      // 경로는 순서대로 왼쪽→오른쪽, 근거는 연결된 것끼리 뭉치게.
      setGraph(buildGraphologyGraph(rawGraph.nodes, rawGraph.edges, {
        layout: highlight.mode === "path" ? "sequence" : "force",
        order: highlight.keys,
        colorBy: "community",
        emphasizeSeeds: true,
        pathEdges: highlight.mode === "path" ? highlight.keys : null,
      }));
      return;
    }
    if (isMapOverview) {
      setGraph(buildCommunityMapGraph(rawGraph.nodes, rawGraph.edges));
      return;
    }
    if (isDomainView) {
      setGraph(buildDomainGraph(rawGraph.nodes, rawGraph.edges));
      return;
    }
    if (isFunctionView) {
      setGraph(buildFunctionGraph(rawGraph.nodes, rawGraph.edges, VIEW_LAYERS[view]));
      return;
    }
    // 포커스가 걸려있으면 dagre가 스캔 전체가 아니라 그 주변 N-hop만
    // 레이아웃하게 만든다 — hairball(노드 수백~천 개가 한 화면에 뭉치는
    // 문제)의 원인은 "레이아웃 자체가 항상 전체를 대상으로 돈다"는 것이라,
    // 클릭 후 흐리게 하는 기존 기능(레이아웃 후처리)로는 안 고쳐진다.
    const { nodes, edges } = focusSymbol
      ? filterByFocus(rawGraph.nodes, rawGraph.edges, focusSymbol, focusDepth)
      : rawGraph;
    // 모듈 안(드릴다운)은 모듈 색 + force 배치 — 계층 없는 호출 묶음이라 한 줄로 늘어뜨리지 않는다.
    setGraph(buildGraphologyGraph(nodes, edges, {
      hotspotMode,
      maxRecentChanges,
      layout: isMapView && !focusSymbol ? "force" : "dagre",
      colorBy: isMapView ? "community" : "kind",
    }));
  }, [rawGraph, hotspotMode, maxRecentChanges, isDomainView, isFunctionView, view, focusSymbol, focusDepth,
      isMapView, isMapOverview, isHighlight, highlight, dataKind]);

  const filteredFunctions = functionFilter.trim()
    ? functions.filter((f) => {
        const q = functionFilter.toLowerCase();
        return f.qualified_name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q);
      })
    : functions;
  // 위험 탐지 패널에서 jumpToFunction()으로 골라진 함수가 필터/200개 자르기에
  // 걸려 <select> 옵션 목록에서 빠지면 드롭다운 표시가 어긋나므로 항상 포함시킨다.
  const selectedFunctionInList = functions.find((f) => f.symbol_key === selectedFunction);
  const functionOptions =
    selectedFunctionInList && !filteredFunctions.includes(selectedFunctionInList)
      ? [selectedFunctionInList, ...filteredFunctions]
      : filteredFunctions;

  const isSymbolView = !isDomainView && !isFunctionView && !isMapOverview && !isHighlight;
  const allNodes = rawGraph && isSymbolView ? rawGraph.nodes : [];
  const focusOptions = focusFilter.trim()
    ? allNodes.filter((n) => {
        const q = focusFilter.toLowerCase();
        return (n.data.label || "").toLowerCase().includes(q) || (n.data.path || "").toLowerCase().includes(q);
      })
    : allNodes;

  const runSearch = async () => {
    if (!searchQuery.trim() || !scanId) return;
    setFindings(null);
    setAskOpen(false);
    setSearching(true);
    try {
      const results = await searchSymbols(scanId, searchQuery.trim());
      setSearchResults(results);
    } catch (err) {
      console.error(err);
      setSearchResults([]);
    } finally {
      setSearching(false);
    }
  };

  const loadFindings = async (mode) => {
    if (!scanId) return;
    setSearchResults(null);
    setAskOpen(false);
    setFindingsMode(mode);
    setFindingsLoading(true);
    try {
      const { findings: rows } = await fetchDataflow(scanId, mode);
      setFindings(rows);
    } catch (err) {
      console.error(err);
      setFindings([]);
    } finally {
      setFindingsLoading(false);
    }
  };

  const toggleFindings = () => {
    if (findings) {
      setFindings(null);
      return;
    }
    loadFindings(findingsMode);
  };

  const jumpToFunction = (symbolKey) => {
    setView("pdg");
    setSelectedFunction(symbolKey);
  };

  const runAsk = async () => {
    if (!askInput.trim() || !scanId) return;
    setSearchResults(null);
    setFindings(null);
    setAskEvents([]);
    setAskAnswerText("");
    setAskFinal(null);
    setAsking(true);
    askAbortRef.current?.abort();
    const controller = new AbortController();
    askAbortRef.current = controller;
    try {
      await streamAskQuestion(scanId, askInput.trim(), {
        signal: controller.signal,
        onEvent: (event) => setAskEvents((prev) => [...prev, event]),
        onChunk: (delta) => setAskAnswerText((prev) => prev + delta),
        onDone: (payload) => {
          setAskFinal({ answer: payload.answer, tools_used: payload.tools_used, evidence: payload.evidence });
          // S5 — 답변 근거 심볼만 서브그래프로 바꿔 보여준다.
          const keys = evidenceSymbolKeys(payload.evidence);
          if (keys.length > 0) setHighlight({ keys, mode: "evidence", title: "AI 답변 근거" });
        },
        onError: (message) => setAskFinal({ answer: message, tools_used: [], evidence: {} }),
      });
    } catch (err) {
      console.error(err);
      setAskFinal({ answer: "질문을 처리하는 중 오류가 발생했습니다.", tools_used: [], evidence: {} });
    } finally {
      setAsking(false);
    }
  };

  const toggleAsk = () => {
    setAskOpen((open) => {
      if (open) {
        askAbortRef.current?.abort();
        setAskEvents([]);
        setAskAnswerText("");
        setAskFinal(null);
      }
      return !open;
    });
  };

  // 모듈 지도 드릴다운 — 모듈 하나의 심볼 + 바깥 이웃만 다시 불러온다.
  const openCommunity = (id) => {
    setFocusSymbol("");
    setFocusFilter("");
    setCodeView(null);
    setSelectedCommunity(id);
  };

  const backToMap = () => {
    setFocusSymbol("");
    setFocusFilter("");
    setSelectedCommunity(null);
  };

  // 요약 카드의 핵심 심볼 — 전체 심볼 그래프에서 그 주변 2-hop 만 연다.
  const focusKeySymbol = (symbolKey) => {
    setView("all");
    setFocusDepth(2);
    setFocusSymbol(symbolKey);
  };

  const askSuggested = (question) => {
    setSearchResults(null);
    setFindings(null);
    setAskInput(question);
    setAskOpen(true);
  };

  // AST 뷰: 노드 자체가 path/startLine 을 담고 있다(외부 노드는 path 없음).
  // 함수 뷰(CFG/PDG/CPG): 블록 노드는 startLine 만 있고 path 는 현재 선택된
  // 함수(functionMeta)의 것을 쓴다 — 블록이 속한 파일은 하나뿐이라서다.
  const handleNodeClick = (attrs) => {
    if (isMapOverview) {
      if (attrs.communityId !== undefined) openCommunity(attrs.communityId);
      return;
    }
    if (isFunctionView) {
      if (functionMeta?.path && attrs.startLine) {
        setCodeView({ path: functionMeta.path, line: attrs.startLine });
      }
      return;
    }
    if (!isDomainView && attrs.path) {
      setCodeView({ path: attrs.path, line: attrs.startLine });
    }
  };

  return (
    <div className="graph-wrap">
      <div className="graph-toolbar">
        <select
          className="graph-domain-select"
          value={view}
          onChange={(e) => {
            setHighlight(null);
            setView(e.target.value);
          }}
        >
          {VIEW_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <button onClick={toggleAsk}>{askOpen ? "AI에게 질문 닫기" : "AI에게 질문"}</button>
        {isFunctionView && (
          <>
            <input
              className="graph-search-input"
              placeholder="함수 이름/경로로 찾기"
              value={functionFilter}
              onChange={(e) => setFunctionFilter(e.target.value)}
            />
            <select
              className="graph-domain-select"
              value={selectedFunction}
              onChange={(e) => setSelectedFunction(e.target.value)}
            >
              <option value="">함수 선택 ({filteredFunctions.length}개)</option>
              {functionOptions.slice(0, 200).map((f) => (
                <option key={f.symbol_key} value={f.symbol_key}>
                  {f.path} · {f.qualified_name}
                </option>
              ))}
            </select>
            {functionMeta && (
              <span className="graph-stats-inline">
                {functionMeta.path} · {functionMeta.qualified_name}
              </span>
            )}
          </>
        )}
        <button onClick={toggleFindings} disabled={findingsLoading}>
          {findingsLoading ? "탐지 중..." : findings ? "위험 탐지 닫기" : "위험 탐지"}
        </button>
        {isSymbolView && (
          <>
            <details className="graph-filters">
            <summary>필터{focusSymbol || hotspotMode || selectedDomain || includeUnresolved ? " ●" : ""}</summary>
            <div className="graph-filters-body">
            <input
              className="graph-search-input"
              placeholder="이 함수 주변만 보기 — 이름으로 찾기"
              value={focusFilter}
              onChange={(e) => setFocusFilter(e.target.value)}
            />
            <select
              className="graph-domain-select"
              value={focusSymbol}
              onChange={(e) => setFocusSymbol(e.target.value)}
            >
              <option value="">주변 보기 없음(전체 표시)</option>
              {focusOptions.slice(0, 200).map((n) => (
                <option key={n.data.id} value={n.data.id}>
                  {n.data.path ? `${n.data.path} · ` : ""}{n.data.label}
                </option>
              ))}
            </select>
            {focusSymbol && (
              <label className="graph-toggle">
                깊이
                <select
                  className="graph-domain-select"
                  value={focusDepth}
                  onChange={(e) => setFocusDepth(Number(e.target.value))}
                >
                  {[1, 2, 3].map((d) => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              </label>
            )}
            {!isMapView && (
              <label className="graph-toggle">
                <input
                  type="checkbox"
                  checked={includeUnresolved}
                  onChange={(e) => setIncludeUnresolved(e.target.checked)}
                />
                외부 라이브러리 호출 포함
              </label>
            )}
            <label className="graph-toggle">
              <input
                type="checkbox"
                checked={hotspotMode}
                onChange={(e) => setHotspotMode(e.target.checked)}
              />
              변경 빈도 히트맵
            </label>
            {domains.length > 0 && !isMapView && (
              <select
                className="graph-domain-select"
                value={selectedDomain}
                onChange={(e) => setSelectedDomain(e.target.value)}
              >
                <option value="">전체 도메인</option>
                {domains.map(({ domain, fileCount }) => (
                  <option key={domain} value={domain}>
                    {domain} ({fileCount}개 파일)
                  </option>
                ))}
              </select>
            )}
            </div>
            </details>
            <input
              className="graph-search-input"
              placeholder="코드 의미 검색 (예: 로그인 처리하는 함수)"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && runSearch()}
            />
            <button onClick={runSearch} disabled={searching}>
              {searching ? "검색 중..." : "검색"}
            </button>
          </>
        )}
        {stats && (
          <span className="graph-stats-inline">
            {isMapOverview ? `모듈 ${stats.nodes}개 · 심볼 ${codeMap?.symbol_count ?? "-"}개` : `노드 ${stats.nodes}개 · 관계 ${stats.edges}개`}
          </span>
        )}
      </div>
      {isHighlight && (
        <div className="code-map-bar code-map-highlight-bar">
          <span className="code-map-crumb is-current">
            <span className="code-map-dot code-map-dot-seed" />
            {highlight.title || (highlight.mode === "path" ? "흐름 경로" : "근거")}
          </span>
          <span className="muted code-map-hint">
            {highlight.mode === "path"
              ? `경로의 함수 ${highlight.keys.length}개를 순서대로 표시합니다.`
              : `근거 함수 ${highlight.keys.length}개(굵은 원)와 바로 연결된 함수만 표시합니다.`}
          </span>
          <button type="button" className="code-map-crumb" onClick={() => setHighlight(null)}>전체 지도로 돌아가기</button>
        </div>
      )}
      {isMapView && !isHighlight && (
        <div className="code-map-bar">
          <nav className="code-map-crumbs" aria-label="코드 지도 위치">
            {isMapOverview ? (
              <span className="code-map-crumb is-current">전체 모듈</span>
            ) : (
              <>
                <button type="button" className="code-map-crumb" onClick={backToMap}>전체 모듈</button>
                <span className="code-map-crumb-sep">›</span>
                <span className="code-map-crumb is-current">
                  <span className="code-map-dot" style={{ background: communityColor(selectedCommunity) }} />
                  {currentCommunity?.name || `모듈 ${selectedCommunity}`}
                </span>
                {currentCommunity?.top_dir && <span className="muted code-map-dir">{currentCommunity.top_dir}</span>}
              </>
            )}
          </nav>
          {isMapOverview && <span className="muted code-map-hint">원을 누르면 그 모듈의 함수들이 펼쳐집니다.</span>}
          {!isMapOverview && <span className="muted code-map-hint">함수를 누르면 코드가 열립니다. 회색 점은 모듈 밖 연결입니다.</span>}
        </div>
      )}
      {isMapOverview && codeMap?.report && (codeMap.report.key_symbols?.length > 0 || codeMap.report.questions?.length > 0) && (
        <div className="code-map-summary">
          {codeMap.report.key_symbols?.length > 0 && (
            <div className="code-map-summary-col">
              <div className="code-map-summary-title">핵심 함수 (연결이 가장 많음)</div>
              <div className="code-map-chips">
                {codeMap.report.key_symbols.map((k) => (
                  <button key={k.symbol_key} type="button" className="code-map-chip" onClick={() => focusKeySymbol(k.symbol_key)}
                          title={`${k.degree}개 연결 — 주변 보기`}>
                    {k.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          {codeMap.report.questions?.length > 0 && (
            <div className="code-map-summary-col">
              <div className="code-map-summary-title">이런 걸 물어보세요</div>
              <div className="code-map-chips">
                {codeMap.report.questions.slice(0, 3).map((q) => (
                  <button key={q} type="button" className="code-map-chip is-question" onClick={() => askSuggested(q)}>
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
      <div className="graph-body">
      <div className="graph-canvas-wrap">
        {status === "loading" && (
          <div className="graph-overlay">
            불러오는 중...
            {includeUnresolved && " (노드가 많아 레이아웃 계산에 몇 초 걸릴 수 있습니다)"}
          </div>
        )}
        {status === "error" && <div className="graph-overlay">그래프를 불러오지 못했습니다.</div>}
        {status === "ready" && isDomainView && stats?.nodes === 0 && (
          <div className="graph-overlay">아직 도메인 태깅이 없습니다 (symbol.annotate 대기 중).</div>
        )}
        {isFunctionView && !selectedFunction && !isHighlight && (
          <div className="graph-overlay">위에서 함수를 먼저 선택하세요.</div>
        )}
        <SigmaContainer
          className="graph-canvas"
          settings={{
            defaultEdgeType: "arrow",
            defaultNodeType: ICON_NODE_TYPE,
            nodeProgramClasses: NODE_PROGRAM_CLASSES,
            defaultDrawNodeLabel: drawNodeLabelBelow,
            labelRenderedSizeThreshold: 9, // 줌아웃해서 노드가 화면상 9px보다 작아지면 라벨 숨김
            labelSize: 11,
            // 라벨 격자 — 칸마다 큰 노드 라벨만 남겨 겹침을 막는다(줌인하면 더 보임)
            labelDensity: 0.6,
            labelGridCellSize: 90,
          }}
        >
          {graph && <GraphLoader graph={graph} />}
          {isHighlight && graph && <SeedPulse graph={graph} />}
          <GraphInteractions
            key={`${view}:${selectedCommunity}`}
            externalFocus={focusRequest}
            onEdgeClick={isFunctionView ? setEdgeInfo : undefined}
            onNodeClick={isDomainView ? undefined : handleNodeClick}
          />
          <div className="graph-minimap-wrap">
            <MiniMap width="220px" height="160px" />
          </div>
          <ControlsContainer position="bottom-left">
            <ZoomControl />
          </ControlsContainer>
        </SigmaContainer>
        {hotspotMode && isSymbolView && (
          <div className="graph-legend">
            <div className="graph-legend-title">최근 변경 빈도</div>
            <div
              className="graph-legend-bar"
              style={{
                background: `linear-gradient(to right, ${heatColor(0)}, ${heatColor(0.33)}, ${heatColor(0.66)}, ${heatColor(1)})`,
              }}
            />
            <div className="graph-legend-scale">
              <span>0</span>
              <span>{maxRecentChanges}회</span>
            </div>
          </div>
        )}
        {isFunctionView && selectedFunction && !edgeInfo && (view === "pdg" || view === "cpg") && (
          <div className="graph-legend">
            <div className="graph-legend-title">엣지 종류 (클릭하면 근거 표시)</div>
            {view === "cpg" && (
              <div className="graph-legend-row"><span className="graph-legend-swatch" style={{ background: "#94a3b8" }} />제어 흐름(CFG)</div>
            )}
            <div className="graph-legend-row"><span className="graph-legend-swatch" style={{ background: "#3b82f6" }} />데이터 의존(PDG)</div>
            <div className="graph-legend-row"><span className="graph-legend-swatch" style={{ background: "#f97316" }} />제어 의존(PDG)</div>
          </div>
        )}
        {isFunctionView && edgeInfo && (
          <div className="graph-legend">
            <div className="graph-legend-title">
              엣지 근거
              <button className="graph-search-close" onClick={() => setEdgeInfo(null)}>×</button>
            </div>
            <div className="graph-legend-row">{edgeInfo.sourceLabel} → {edgeInfo.targetLabel}</div>
            {edgeInfo.layer === "data" ? (
              <>
                <div className="graph-legend-row">변수: <code>{edgeInfo.var}</code></div>
                <div className="graph-legend-row">{EVIDENCE_EXPLANATIONS[edgeInfo.evidenceKind] || edgeInfo.evidenceKind}</div>
              </>
            ) : (
              <div className="graph-legend-row">분기: {edgeInfo.branchLabel}</div>
            )}
          </div>
        )}
        {askOpen && (
          <div className="graph-search-results graph-ask-panel">
            <div className="graph-search-results-header">
              <span>AI에게 질문</span>
              <button className="graph-search-close" onClick={toggleAsk}>×</button>
            </div>
            <div className="graph-ask-form">
              <input
                className="graph-search-input"
                placeholder="예: 이메일 인증 토큰 처리하는 함수 어디 있어?"
                value={askInput}
                onChange={(e) => setAskInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && runAsk()}
                autoFocus
              />
              <button onClick={runAsk} disabled={asking}>
                {asking ? "생각 중..." : "질문"}
              </button>
            </div>
            {askEvents.length > 0 && <AgentActivityTimeline events={askEvents} live={asking} />}
            {!askFinal && (askAnswerText || asking) && (
              <p className="graph-ask-answer-text">{askAnswerText || "생각하는 중..."}</p>
            )}
            {askFinal && (
              <div className="graph-ask-answer">
                {askFinal.tools_used.length > 0 && (
                  <div className="graph-finding-evidence">
                    {askFinal.tools_used.map((t) => (
                      <span key={t} className="badge">{TOOL_LABELS[t] || t}</span>
                    ))}
                  </div>
                )}
                <p className="graph-ask-answer-text">{askFinal.answer}</p>
                {Object.entries(askFinal.evidence || {}).map(([tool, rows]) => {
                  if (!Array.isArray(rows) || rows.length === 0) return null;
                  if (tool === "semantic_search") {
                    return (
                      <div key={tool} className="graph-ask-evidence-group">
                        <div className="graph-ask-evidence-title">{TOOL_LABELS[tool]} 근거</div>
                        <ul>
                          {rows.map((r) => (
                            <li key={r.symbol_key}>
                              <button
                                className="graph-finding-hop"
                                onClick={() => setFocusRequest({ nodeId: r.symbol_key, ts: Date.now() })}
                              >
                                {r.path} · {r.qualified_name}
                              </button>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  }
                  if (tool === "dataflow_security") {
                    return (
                      <div key={tool} className="graph-ask-evidence-group">
                        <div className="graph-ask-evidence-title">{TOOL_LABELS[tool]} 근거</div>
                        <ul>
                          {rows.map((f, i) => {
                            const hops = collapsePath(f.path);
                            return (
                              <li key={i} className="graph-finding-path">
                                {hops.map((hop, j) => (
                                  <span key={j}>
                                    <button className="graph-finding-hop" onClick={() => jumpToFunction(hop.symbol_key)}>
                                      {hop.qualified_name || hop.symbol_key}
                                    </button>
                                    {j < hops.length - 1 && " → "}
                                  </span>
                                ))}
                                {" → "}
                                <span className="graph-finding-sink">{f.sink_call}</span>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    );
                  }
                  if (tool === "dataflow_business") {
                    return (
                      <div key={tool} className="graph-ask-evidence-group">
                        <div className="graph-ask-evidence-title">{TOOL_LABELS[tool]} 근거</div>
                        <ul>
                          {rows.map((f, i) => (
                            <li key={i} className="graph-finding-path">
                              <button className="graph-finding-hop" onClick={() => jumpToFunction(f.caller_symbol_key)}>
                                {f.caller_qualified_name || f.caller_symbol_key}
                              </button>
                              {" → "}
                              <button className="graph-finding-hop" onClick={() => jumpToFunction(f.callee_symbol_key)}>
                                {f.callee_qualified_name || f.callee_symbol_key}
                              </button>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  }
                  return null;
                })}
              </div>
            )}
          </div>
        )}
        {searchResults && (
          <div className="graph-search-results">
            <div className="graph-search-results-header">
              검색 결과 {searchResults.length}개
              <button className="graph-search-close" onClick={() => setSearchResults(null)}>
                ×
              </button>
            </div>
            {searchResults.length === 0 && (
              <div className="graph-search-empty">
                결과가 없습니다 (임베딩이 아직 안 만들어졌을 수 있습니다).
              </div>
            )}
            <ul>
              {searchResults.map((r) => (
                <li
                  key={r.symbol_key}
                  onClick={() => setFocusRequest({ nodeId: r.symbol_key, ts: Date.now() })}
                >
                  <div className="graph-search-result-path">
                    {r.path} · {(r.similarity * 100).toFixed(0)}%
                  </div>
                  <div className="graph-search-result-name">{r.qualified_name}</div>
                  {r.summary && <div className="graph-search-result-summary">{r.summary}</div>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {findings && (
          <div className="graph-search-results">
            <div className="graph-search-results-header">
              <span>
                {findingsMode === "security" ? "보안 위험 탐지" : "비즈니스 IP 흐름"} {findings.length}건
              </span>
              <button className="graph-search-close" onClick={() => setFindings(null)}>
                ×
              </button>
            </div>
            <div className="graph-finding-mode-tabs">
              <button
                className={findingsMode === "security" ? "active" : undefined}
                onClick={() => loadFindings("security")}
                disabled={findingsLoading}
              >
                보안
              </button>
              <button
                className={findingsMode === "business" ? "active" : undefined}
                onClick={() => loadFindings("business")}
                disabled={findingsLoading}
              >
                비즈니스 IP
              </button>
            </div>
            {findings.length === 0 && (
              <div className="graph-search-empty">
                {findingsMode === "security"
                  ? "발견된 경로가 없습니다(레이어 4는 함수 경계 하나만 넘습니다 — 더 깊은 체인은 아직 못 찾을 수 있습니다)."
                  : "도메인 경계를 넘는 호출이 없습니다(symbol.annotate 로 도메인 태깅이 먼저 돼 있어야 합니다)."}
              </div>
            )}
            {findingsMode === "security" ? (
              <ul>
                {findings.map((f, i) => {
                  const hops = collapsePath(f.path);
                  return (
                    <li key={i} className="graph-finding-card">
                      <div className="graph-finding-evidence">
                        {f.evidence_kinds.map((k, j) => (
                          <span key={j} className="badge">{k}</span>
                        ))}
                      </div>
                      <div className="graph-finding-path">
                        {hops.map((hop, j) => (
                          <span key={j}>
                            <button
                              className="graph-finding-hop"
                              onClick={() => jumpToFunction(hop.symbol_key)}
                            >
                              {hop.qualified_name || hop.symbol_key}
                            </button>
                            {j < hops.length - 1 && " → "}
                          </span>
                        ))}
                        {" → "}
                        <span className="graph-finding-sink">{f.sink_call}</span>
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <ul>
                {findings.map((f, i) => (
                  <li key={i} className="graph-finding-card">
                    <div className="graph-finding-evidence">
                      <span className="badge">{f.source_domain}</span>
                      <span>→</span>
                      <span className="badge">{f.sink_domain}</span>
                    </div>
                    <div className="graph-finding-path">
                      <button className="graph-finding-hop" onClick={() => jumpToFunction(f.caller_symbol_key)}>
                        {f.caller_qualified_name || f.caller_symbol_key}
                      </button>
                      {" → "}
                      <button className="graph-finding-hop" onClick={() => jumpToFunction(f.callee_symbol_key)}>
                        {f.callee_qualified_name || f.callee_symbol_key}
                      </button>
                      {f.arg_vars.length > 0 && (
                        <div className="graph-finding-sink">인자: {f.arg_vars.join(", ")}</div>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>
      {codeView && (
        <CodeViewer
          scanId={scanId}
          path={codeView.path}
          line={codeView.line}
          onClose={() => setCodeView(null)}
        />
      )}
      </div>
    </div>
  );
}
