// 개발용 미리보기 하네스 — 백엔드 없이 멀티페인 분할 레이아웃(flexlayout)만
// 띄워 드래그 분할/리사이즈/탭 이동을 눈으로 확인한다. 프로덕션 번들과 무관.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import "./App.css";
import { WorkspaceProvider } from "./WorkspaceContext";
import { TeamspaceProvider } from "./TeamspaceContext";
import { ProjectProvider } from "./ProjectContext";
import { TabsProvider, useTabs } from "./TabsContext";
import MultiPaneLayout from "./MultiPaneLayout";

const P1 = { id: "p1", name: "결제 서비스" };
const P2 = { id: "p2", name: "코어뱅킹" };

function Bar() {
  const { openChatTab, openDetailTab, resetLayout } = useTabs();
  return (
    <div style={{ display: "flex", gap: 8, padding: 8, background: "#0f151c", flexShrink: 0 }}>
      <button onClick={() => openChatTab(P1)}>+ 채팅 · 결제</button>
      <button onClick={() => openDetailTab(P1)}>+ 상세 · 결제</button>
      <button onClick={() => openChatTab(P2)}>+ 채팅 · 코어뱅킹</button>
      <button onClick={() => openDetailTab(P2)}>+ 상세 · 코어뱅킹</button>
      <button onClick={resetLayout}>레이아웃 초기화</button>
    </div>
  );
}

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <WorkspaceProvider>
        <TeamspaceProvider>
          <ProjectProvider>
            <TabsProvider>
              <div style={{ position: "fixed", inset: 0, display: "flex", flexDirection: "column" }}>
                <Bar />
                <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
                  <MultiPaneLayout />
                </div>
              </div>
            </TabsProvider>
          </ProjectProvider>
        </TeamspaceProvider>
      </WorkspaceProvider>
    </BrowserRouter>
  </StrictMode>
);
