import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import Sidebar from "./Sidebar";
import TopNavBar from "./TopNavBar";
import { SidebarProvider } from "./SidebarContext";
import { WorkspaceProvider, useWorkspace } from "./WorkspaceContext";
import { TeamspaceProvider } from "./TeamspaceContext";
import { ProjectProvider } from "./ProjectContext";
import { TabsProvider } from "./TabsContext";
import { MascotProvider } from "./MascotContext";
import MascotButton from "./mascot/MascotButton";
import MascotPanel from "./mascot/MascotPanel";
import LoginPage from "./pages/LoginPage";
import VerifyEmailPage from "./pages/VerifyEmailPage";
import OnboardingPage from "./pages/OnboardingPage";
import WorkspaceHomePage from "./pages/WorkspaceHomePage";
import ProjectSettingsPage from "./pages/ProjectSettingsPage";
import MultiPaneLayout from "./MultiPaneLayout";
import WorkspaceInviteAcceptPage from "./pages/WorkspaceInviteAcceptPage";
import TeamspaceInviteAcceptPage from "./pages/TeamspaceInviteAcceptPage";
import ProjectInviteAcceptPage from "./pages/ProjectInviteAcceptPage";
import { fetchGithubAccounts, fetchMe } from "./api";
import "./App.css";

function OnboardingGate({ children }) {
  const { workspaces } = useWorkspace();

  if (workspaces === null) {
    return <div className="app-empty">불러오는 중...</div>;
  }
  if (workspaces.length === 0) {
    return <OnboardingPage />;
  }
  return (
    <div className="app-main-column">
      <TopNavBar />
      <div className="app-main-content">{children}</div>
    </div>
  );
}

function AppLayout() {
  const [me, setMe] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [attempt, setAttempt] = useState(0);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const location = useLocation();

  // 좁은 화면에서는 사이드바가 오버레이라, 페이지를 이동하면(NavLink 클릭 등)
  // 자동으로 닫아야 다음 화면을 가린 채로 남지 않는다.
  useEffect(() => {
    setSidebarOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    // 로그인 안 됐으면 fetchMe 내부(api.js)가 401을 잡아 /login으로 풀 페이지
    // 리다이렉트한다 — 여기서 따로 가드할 필요 없음. 여기 catch는 그것과
    // 다른 경우(백엔드 재시작 중 등 진짜 네트워크 실패)를 위한 것이라,
    // 조용히 삼키면 "불러오는 중..."에서 영영 멈춘다 — 재시도 버튼을 준다.
    fetchMe().then((m) => {
      if (cancelled) return;
      if (m.email_verified === false) {
        // 미인증 계정 — 다른 API 는 전부 403 이라 화면을 그리기 전에 인증 화면으로 보낸다.
        window.location.href = `/verify-email?next=${encodeURIComponent(window.location.pathname)}`;
        return;
      }
      setMe(m);
      // 아바타는 더 이상 /me에 없다 — 연결된 GitHub 계정이 있으면 거기서 가져온다.
      fetchGithubAccounts()
        .then((accounts) => {
          if (!cancelled && accounts[0]?.avatar_url) {
            setMe((prev) => ({ ...prev, avatar_url: accounts[0].avatar_url }));
          }
        })
        .catch(() => {});
    }).catch((err) => {
      if (!cancelled) setLoadError(err.message);
    });
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  if (loadError) {
    return (
      <div className="app-empty app-empty-error">
        <p>불러오지 못했습니다: {loadError}</p>
        <button className="btn-primary" onClick={() => setAttempt((n) => n + 1)}>
          다시 시도
        </button>
      </div>
    );
  }

  if (!me) {
    return <div className="app-empty">불러오는 중...</div>;
  }

  return (
    <WorkspaceProvider>
      <TeamspaceProvider>
        <ProjectProvider>
          <TabsProvider>
            <MascotProvider>
              <SidebarProvider>
                <div className={`app-shell${sidebarOpen ? " sidebar-open" : ""}`}>
                  <button
                    type="button"
                    className="mobile-sidebar-toggle"
                    onClick={() => setSidebarOpen((v) => !v)}
                    aria-label={sidebarOpen ? "메뉴 닫기" : "메뉴 열기"}
                  >
                    {sidebarOpen ? "✕" : "☰"}
                  </button>
                  <Sidebar me={me} />
                  {sidebarOpen && <div className="sidebar-backdrop" onClick={() => setSidebarOpen(false)} />}
                  <OnboardingGate>
                    <Routes>
                      <Route path="/workspace" element={<WorkspaceHomePage />} />
                      <Route path="/projects/:projectId/settings" element={<ProjectSettingsPage />} />
                      {/* 채팅/상세는 라우트가 아니라 멀티페인 셸 안의 패널로 열린다.
                          아래 경로들은 딥링크 진입점으로만 남겨 둔다(셸이 패널을 열고 /panes로 정리). */}
                      <Route path="/panes" element={<MultiPaneLayout />} />
                      <Route path="/projects/:projectId/chat" element={<MultiPaneLayout />} />
                      <Route path="/projects/:projectId/detail/*" element={<MultiPaneLayout />} />
                      <Route path="/" element={<Navigate to="/workspace" replace />} />
                      <Route path="*" element={<Navigate to="/workspace" replace />} />
                    </Routes>
                  </OnboardingGate>
                  <MascotButton />
                  <MascotPanel />
                </div>
              </SidebarProvider>
            </MascotProvider>
          </TabsProvider>
        </ProjectProvider>
      </TeamspaceProvider>
    </WorkspaceProvider>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        {/* 로그인 여부와 무관하게 미리보기를 보여줘야 해서 AppLayout(강제 로그인) 밖에 둔다 */}
        <Route path="/workspace-invites/:token" element={<WorkspaceInviteAcceptPage />} />
        <Route path="/teamspace-invites/:token" element={<TeamspaceInviteAcceptPage />} />
        <Route path="/project-invites/:token" element={<ProjectInviteAcceptPage />} />
        <Route path="/*" element={<AppLayout />} />
      </Routes>
    </BrowserRouter>
  );
}
