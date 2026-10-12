import { Route } from "react-router-dom";
import DashboardPage from "./pages/DashboardPage";
import GraphViewerPage from "./pages/GraphViewerPage";
import CIStatusPage from "./pages/CIStatusPage";
import RepositoryFilesPage from "./pages/RepositoryFilesPage";
import RepositoryBranchesPage from "./pages/RepositoryBranchesPage";
import RepositoryCommitsPage from "./pages/RepositoryCommitsPage";
import RepositoryPullRequestsPage from "./pages/RepositoryPullRequestsPage";
import ReleasesListPage from "./pages/ReleasesListPage";
import ReleaseDetailPage from "./pages/ReleaseDetailPage";
import SecurityFindingsPage from "./pages/SecurityFindingsPage";
import SecurityRiskPage from "./pages/SecurityRiskPage";
import RiskSymbolPage from "./pages/RiskSymbolPage";
import SecurityRiskHistoryPage from "./pages/SecurityRiskHistoryPage";
import SecurityTimelinePage from "./pages/SecurityTimelinePage";
import RiskListPage from "./pages/RiskListPage";
import RiskLifecyclePage from "./pages/RiskLifecyclePage";
import SecurityWhitelistPage from "./pages/SecurityWhitelistPage";
import SecurityAiSettingsPage from "./pages/SecurityAiSettingsPage";

// 상세보기(Overview/Repository/Releases/Security)의 하위 라우트 목록.
// 각 상세보기 패널은 이 목록을 자기만의 MemoryRouter 안에서 렌더한다 —
// 그래서 패널마다 독립적으로 하위 탭을 오갈 수 있고, 브라우저 URL은
// 멀티페인 셸(/panes)에 고정된 채로 유지된다.
// &lt;Route path="/projects/:projectId/detail" element={&lt;ProjectDetailPage/&gt;}&gt;
//   {detailChildRoutes()}
// &lt;/Route&gt; 형태로 부모 라우트의 자식으로 펼쳐 쓴다.
export function detailChildRoutes() {
  return (
    <>
      <Route path="overview" element={<DashboardPage />} />
      <Route path="graph/:scanId" element={<GraphViewerPage />} />
      <Route path="graph" element={<GraphViewerPage />} />
      <Route path="repository/files" element={<RepositoryFilesPage />} />
      <Route path="repository/branches" element={<RepositoryBranchesPage />} />
      <Route path="repository/commits" element={<RepositoryCommitsPage />} />
      <Route path="repository/pull-requests" element={<RepositoryPullRequestsPage />} />
      <Route path="releases" element={<ReleasesListPage />} />
      <Route path="releases/:releaseId" element={<ReleaseDetailPage />} />
      <Route path="security/scan" element={<CIStatusPage />} />
      <Route path="security/findings" element={<SecurityFindingsPage />} />
      <Route path="security/risk" element={<SecurityRiskPage />} />
      <Route path="security/risk/:symbolKey" element={<RiskSymbolPage />} />
      <Route path="security/timeline" element={<SecurityTimelinePage />} />
      <Route path="security/risks" element={<RiskListPage />} />
      <Route path="security/risks/:fingerprint" element={<RiskLifecyclePage />} />
      <Route path="security/risk-history" element={<SecurityRiskHistoryPage />} />
      <Route path="security/whitelist" element={<SecurityWhitelistPage />} />
      <Route path="security/ai" element={<SecurityAiSettingsPage />} />
    </>
  );
}
