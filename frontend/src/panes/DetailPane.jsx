import { useEffect, useRef } from "react";
import {
  MemoryRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
  UNSAFE_LocationContext,
  UNSAFE_RouteContext,
} from "react-router-dom";
import ProjectDetailPage from "../pages/ProjectDetailPage";
import { detailChildRoutes } from "../detailRoutes";

const defaultPath = (projectId) => `/projects/${projectId}/detail/overview`;
const FRESH_ROUTE_CONTEXT = { outlet: null, matches: [], isDataRoute: false };

// 하위 라우트가 바뀔 때마다 현재 경로를 flexlayout 노드 config에 저장한다
// (새로고침 후 레이아웃 복원 시 그 패널이 보던 하위 화면까지 되살리기 위함).
function PathSync({ onPath }) {
  const loc = useLocation();
  const onPathRef = useRef(onPath);
  useEffect(() => {
    onPathRef.current = onPath;
  });
  useEffect(() => {
    onPathRef.current(loc.pathname + loc.search);
  }, [loc.pathname, loc.search]);
  return null;
}

// 이미 열린 패널에 openDetailTab(project, subPath)가 다시 오면 그 경로로 이동한다.
// nonce 가 바뀔 때만 반응 — 같은 요청이 리렌더마다 반복 이동시키지 않게.
function NavRequestSync({ navRequest }) {
  const navigate = useNavigate();
  const lastNonce = useRef(navRequest?.nonce);
  useEffect(() => {
    if (!navRequest?.path || navRequest.nonce === lastNonce.current) return;
    lastNonce.current = navRequest.nonce;
    navigate(navRequest.path);
  }, [navRequest?.nonce, navRequest?.path, navigate]);
  return null;
}

// 각 상세보기 패널은 자체 MemoryRouter를 갖는다 — DetailTabBar의 NavLink와
// 하위 페이지들의 useNavigate/useParams/useSearchParams가 그대로 동작하면서도
// 브라우저 URL은 건드리지 않는다(멀티페인 셸은 /panes에 머문다).
//
// react-router는 <Router>가 다른 <Router>(App의 BrowserRouter) 안에 중첩되면
// 예외를 던진다(useInRouterContext = LocationContext != null 검사). 그래서
// 안쪽 MemoryRouter 앞에서 LocationContext/RouteContext를 초기값으로 되돌려
// "라우터 밖"인 것처럼 만든다.
export default function DetailPane({ projectId, initialPath, navRequest, onPathChange }) {
  const start =
    initialPath && initialPath.startsWith(`/projects/${projectId}/detail`)
      ? initialPath
      : defaultPath(projectId);

  return (
    <UNSAFE_LocationContext.Provider value={null}>
      <UNSAFE_RouteContext.Provider value={FRESH_ROUTE_CONTEXT}>
        <MemoryRouter initialEntries={[start]}>
          <Routes>
            <Route path="/projects/:projectId/detail" element={<ProjectDetailPage />}>
              {detailChildRoutes()}
            </Route>
            <Route path="*" element={<Navigate to={defaultPath(projectId)} replace />} />
          </Routes>
          <PathSync onPath={onPathChange} />
          <NavRequestSync navRequest={navRequest} />
        </MemoryRouter>
      </UNSAFE_RouteContext.Provider>
    </UNSAFE_LocationContext.Provider>
  );
}
