import { NavLink, useLocation } from "react-router-dom";

const REPOSITORY_TABS = [
  { to: "files", label: "Files" },
  { to: "branches", label: "Branches" },
  { to: "commits", label: "Commits" },
  { to: "pull-requests", label: "Pull Requests" },
];

const SECURITY_TABS = [
  { to: "scan", label: "Scan" },
  { to: "findings", label: "Findings" },
  { to: "risk", label: "Risk" },
  { to: "timeline", label: "Timeline" },
  { to: "risks", label: "Risks" },
  { to: "risk-history", label: "Risk History" },
  { to: "whitelist", label: "Whitelist" },
  { to: "engagements", label: "Engagements" },
  { to: "ai", label: "AI" },
];

// 옛 ProjectNav(좌측 서브 내비게이션)를 대체하는 상세보기 전용 상단 탭바.
// Repository/Security Analysis는 하위 항목이 여럿이라 1차 탭이 활성화되면
// 2차 탭 줄이 추가로 뜬다.
export default function DetailTabBar({ projectId }) {
  const location = useLocation();
  const base = `/projects/${projectId}/detail`;
  const isRepository = location.pathname.startsWith(`${base}/repository`);
  const isSecurity = location.pathname.startsWith(`${base}/security`);
  const isGraph = location.pathname.startsWith(`${base}/graph`);

  return (
    <div className="detail-tab-bar">
      <div className="detail-tab-bar-primary">
        <NavLink to={`${base}/overview`} className={({ isActive }) => (isActive ? "active" : "")}>
          Overview
        </NavLink>
        <NavLink to={`${base}/repository/files`} className={() => (isRepository ? "active" : "")}>
          Repository
        </NavLink>
        <NavLink to={`${base}/graph`} className={() => (isGraph ? "active" : "")}>
          Code Map
        </NavLink>
        <NavLink to={`${base}/releases`} className={({ isActive }) => (isActive ? "active" : "")}>
          Releases
        </NavLink>
        <NavLink to={`${base}/security/scan`} className={() => (isSecurity ? "active" : "")}>
          Security Analysis
        </NavLink>
      </div>

      {isRepository && (
        <div className="detail-tab-bar-secondary">
          {REPOSITORY_TABS.map((t) => (
            <NavLink key={t.to} to={`${base}/repository/${t.to}`} className={({ isActive }) => (isActive ? "active" : "")}>
              {t.label}
            </NavLink>
          ))}
        </div>
      )}

      {isSecurity && (
        <div className="detail-tab-bar-secondary">
          {SECURITY_TABS.map((t) => (
            <NavLink key={t.to} to={`${base}/security/${t.to}`} className={({ isActive }) => (isActive ? "active" : "")}>
              {t.label}
            </NavLink>
          ))}
        </div>
      )}
    </div>
  );
}
