import { useCallback, useEffect, useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Layout } from "flexlayout-react";
import "./vendor/flexlayout-dark.css";
import { useTabs } from "./TabsContext";
import { useProject } from "./ProjectContext";
import { makePaneFactory } from "./panes/paneFactory";

const CHAT_RE = /^\/projects\/([^/]+)\/chat\/?$/;
const DETAIL_RE = /^\/projects\/([^/]+)\/detail(\/.*)?$/;

// 멀티페인 셸. app-main-content 안의 단일 <Outlet> 대신 이 컴포넌트가
// flexlayout <Layout>을 렌더한다. 딥링크(/projects/:id/chat 등)로 들어오면
// 해당 패널을 열고 URL을 /panes로 정리한다.
export default function MultiPaneLayout() {
  const { model, hasTabs, updateNodeConfig, resetLayout, openChatTab, openDetailTab } = useTabs();
  const { projects } = useProject();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    const chat = CHAT_RE.exec(location.pathname);
    const detail = DETAIL_RE.exec(location.pathname);
    if (!chat && !detail) return;
    const projectId = (chat || detail)[1];
    const project = (projects || []).find((p) => p.id === projectId);
    if (!project) {
      navigate("/panes", { replace: true });
      return;
    }
    if (chat) openChatTab(project);
    else openDetailTab(project);
  }, [location.pathname, projects, openChatTab, openDetailTab, navigate]);

  const factory = useMemo(() => makePaneFactory({ updateNodeConfig }), [updateNodeConfig]);

  // 드래그 중 <Layout>이 리렌더돼도 콜백 정체성이 유지되도록 안정화.
  const onRenderTabSet = useCallback(
    (_node, values) => {
      values.buttons.push(
        <button
          key="reset-layout"
          type="button"
          className="pane-tabset-btn"
          title="레이아웃 초기화"
          onClick={() => {
            if (window.confirm("모든 패널을 닫고 레이아웃을 초기화할까요?")) resetLayout();
          }}
        >
          ⟲
        </button>
      );
    },
    [resetLayout]
  );

  return (
    <div className="pane-layout-root">
      <Layout model={model} factory={factory} onRenderTabSet={onRenderTabSet} realtimeResize />
      {!hasTabs && (
        <div className="pane-empty">
          <p className="pane-empty-title">열린 패널이 없습니다</p>
          <p className="muted">왼쪽 사이드바에서 프로젝트를 열면 채팅·상세 패널이 여기 표시됩니다.</p>
          <p className="muted">패널 탭을 가장자리로 끌어다 놓으면 화면이 좌·우·상·하로 분할됩니다.</p>
        </div>
      )}
    </div>
  );
}
