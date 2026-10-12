import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Actions, DockLocation, Model } from "flexlayout-react";
import { useProject } from "./ProjectContext";

const TabsContext = createContext(null);

// 채팅/상세 패널을 탭으로 담고, 탭을 가장자리로 끌어 화면을 좌·우·상·하로
// 분할(Split)할 수 있는 flexlayout 모델을 관리한다. 레이아웃(분할 트리 +
// 크기 + 각 패널의 하위 경로)은 localStorage에 저장돼 새로고침 후에도 복원된다.
const STORAGE_KEY = "neosdl_pane_layout_v1";

const GLOBAL = {
  tabEnableClose: true,
  tabEnableRename: false,
  tabSetEnableMaximize: true,
  tabSetEnableDeleteWhenEmpty: false, // 마지막 탭을 닫아도 빈 tabset은 남겨 둔다(다음 패널을 받을 자리)
  tabSetMinWidth: 160,
  tabSetMinHeight: 120,
  rootOrientationVertical: false,
};

const emptyJson = () => ({
  global: GLOBAL,
  borders: [],
  layout: { type: "row", weight: 100, children: [{ type: "tabset", weight: 100, children: [] }] },
});

function loadJson() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return emptyJson();
    const parsed = JSON.parse(raw);
    parsed.global = { ...GLOBAL, ...(parsed.global || {}) };
    return parsed;
  } catch {
    return emptyJson();
  }
}

function firstTabsetId(model) {
  let id = null;
  model.visitNodes((n) => {
    if (!id && n.getType() === "tabset") id = n.getId();
  });
  return id;
}

function findTab(model, component, projectId) {
  let found = null;
  model.visitNodes((n) => {
    if (found) return;
    if (
      n.getType() === "tab" &&
      n.getComponent() === component &&
      (n.getConfig() || {}).projectId === projectId
    ) {
      found = n;
    }
  });
  return found;
}

function countTabs(model) {
  let c = 0;
  model.visitNodes((n) => {
    if (n.getType() === "tab") c += 1;
  });
  return c;
}

// 이분할했다가 다시 합치면(마지막 탭을 다른 tabset으로 드래그하거나 닫으면)
// deleteWhenEmpty:false 때문에 빈 tabset이 그대로 남아 화면의 절반을 차지한다.
// tabset이 2개 이상이면 빈 것을 제거해 남은 패널이 100%를 채우게 한다.
// (tabset이 하나뿐이면 빈 상태로 남겨 둔다 — 다음 패널을 받을 자리 + 빈 안내 오버레이)
function pruneEmptyTabsets(model) {
  const tabsets = [];
  model.visitNodes((n) => {
    if (n.getType() === "tabset") tabsets.push(n);
  });
  if (tabsets.length <= 1) return false;
  const empty = tabsets.filter((n) => n.getChildren().length === 0);
  if (empty.length === 0) return false;
  // 남길 tabset 수는 최소 1개. 비어있지 않은 게 있으면 빈 것을 전부 제거한다.
  const keepCount = tabsets.length - empty.length > 0 ? 0 : 1;
  const toRemove = empty.slice(0, empty.length - keepCount);
  toRemove.forEach((n) => model.doAction(Actions.deleteTabset(n.getId())));
  return toRemove.length > 0;
}

export function TabsProvider({ children }) {
  const navigate = useNavigate();
  const { setActiveProjectId } = useProject();
  const [model, setModel] = useState(() => Model.fromJson(loadJson()));
  const [tabCount, setTabCount] = useState(() => countTabs(model));

  // 콜백을 model 리스너 안에서 최신값으로 부르되, 리스너 자체는 재등록하지 않는다.
  const setActiveProjectIdRef = useRef(setActiveProjectId);
  useEffect(() => {
    setActiveProjectIdRef.current = setActiveProjectId;
  }, [setActiveProjectId]);

  const persist = useCallback((m) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(m.toJson()));
    } catch {
      /* 용량 초과 등 — 저장 실패는 조용히 무시(세션 내 동작엔 영향 없음) */
    }
  }, []);

  // 어떤 경로(드래그 분할, 탭 닫기, 프로그램적 doAction)로든 모델이 바뀌면
  // 저장 + 탭 수 갱신 + 활성 프로젝트 동기화. addChangeListener는 <Layout>
  // 마운트 여부와 무관하게 직접 doAction까지 잡아준다.
  //
  // 이 동기화는 "adjusting 무시 + SET_ACTIVE_TABSET 무시 + debounce"가 핵심이다.
  //   - 탭을 잡는 순간 flexlayout은 SET_ACTIVE_TABSET을 dispatch한다. 여기서
  //     setActiveProjectId(→ 사이드바/상단바/전 페이지 리렌더 캐스케이드)를 부르면
  //     드래그가 막 시작되는 프레임에 <Layout>·탭버튼이 리렌더돼 포인터 트래킹이
  //     끊긴다(드래그 미완료 + 프리뷰가 화면에 얼어붙음). SET_ACTIVE_TABSET은
  //     구조를 바꾸지 않으므로 저장/재계산도 필요 없다 — 통째로 무시한다.
  //   - ADJUST_WEIGHTS(스플리터 드래그 중간 프레임)도 무시.
  //   - 나머지는 200ms debounce로 모아서 한 번만 반영.
  useEffect(() => {
    let timer = null;
    const flush = () => {
      timer = null;
      pruneEmptyTabsets(model); // 합쳐진 뒤 남은 빈 tabset 제거 → 남은 패널이 100% 차지
      persist(model);
      const n = countTabs(model);
      setTabCount((prev) => (prev === n ? prev : n));
      const cfg = model.getActiveTabset()?.getSelectedNode()?.getConfig();
      if (cfg?.projectId) setActiveProjectIdRef.current(cfg.projectId);
    };
    const listener = {
      onAfterAction: (action) => {
        if (action?.isAdjusting?.()) return;
        if (action?.type === Actions.SET_ACTIVE_TABSET) return;
        if (timer) clearTimeout(timer);
        timer = setTimeout(flush, 200);
      },
    };
    model.addChangeListener(listener);
    return () => {
      model.removeChangeListener(listener);
      if (timer) {
        clearTimeout(timer);
        persist(model); // 언마운트 직전 마지막 상태는 즉시 저장
      }
    };
  }, [model, persist]);

  // 복원된 레이아웃으로 처음 들어왔을 때, 활성 탭의 프로젝트를 한 번 맞춰준다.
  useEffect(() => {
    if (pruneEmptyTabsets(model)) persist(model); // 저장돼 있던 빈 tabset 정리
    const cfg = model.getActiveTabset()?.getSelectedNode()?.getConfig();
    if (cfg?.projectId) setActiveProjectIdRef.current(cfg.projectId);
  }, [model, persist]);

  const openPane = useCallback(
    (component, project) => {
      if (!project?.id) return;
      const existing = findTab(model, component, project.id);
      if (existing) {
        model.doAction(Actions.selectTab(existing.getId()));
      } else {
        const target = model.getActiveTabset()?.getId() || firstTabsetId(model);
        if (target) {
          const kindLabel = component === "chat" ? "채팅" : "상세";
          model.doAction(
            Actions.addTab(
              {
                type: "tab",
                name: `${kindLabel} · ${project.name}`,
                component,
                config: { projectId: project.id, projectName: project.name },
              },
              target,
              DockLocation.CENTER,
              -1,
              true
            )
          );
        }
      }
      // 탭 열기는 즉시 반영(빈 상태 오버레이 깜빡임 방지) — 리스너 debounce와 별개.
      setTabCount((prev) => {
        const n = countTabs(model);
        return prev === n ? prev : n;
      });
      navigate("/panes");
    },
    [model, navigate]
  );

  const openChatTab = useCallback((project) => openPane("chat", project), [openPane]);
  const openDetailTab = useCallback((project) => openPane("detail", project), [openPane]);

  // 패널이 자기 하위 경로를 노드 config에 되돌려 저장할 때 쓴다.
  const updateNodeConfig = useCallback(
    (nodeId, patch) => {
      const node = model.getNodeById(nodeId);
      if (!node) return;
      model.doAction(
        Actions.updateNodeAttributes(nodeId, { config: { ...(node.getConfig() || {}), ...patch } })
      );
    },
    [model]
  );

  const resetLayout = useCallback(() => {
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* 무시 */
    }
    setModel(Model.fromJson(emptyJson()));
    setTabCount(0);
  }, []);

  const value = useMemo(
    () => ({
      model,
      tabCount,
      hasTabs: tabCount > 0,
      openChatTab,
      openDetailTab,
      updateNodeConfig,
      resetLayout,
    }),
    [model, tabCount, openChatTab, openDetailTab, updateNodeConfig, resetLayout]
  );

  return <TabsContext.Provider value={value}>{children}</TabsContext.Provider>;
}

export function useTabs() {
  const ctx = useContext(TabsContext);
  if (!ctx) throw new Error("useTabs must be used within a TabsProvider");
  return ctx;
}
