import { Outlet, useParams } from "react-router-dom";
import DetailTabBar from "../DetailTabBar";

// 옛 DashboardPage + Repository/Releases/Security Analysis 전체를 감싸는
// "상세보기" 레이아웃. 멀티페인 셸에서 상세 패널을 열면 이 컴포넌트가 각
// 패널의 MemoryRouter 안에서 렌더된다 — 전역 activeProject 동기화는 셸
// (TabsContext)이 활성 탭 기준으로 처리하므로 여기서 따로 하지 않는다.
export default function ProjectDetailPage() {
  const { projectId } = useParams();

  return (
    <div className="project-detail-page">
      <DetailTabBar projectId={projectId} />
      <div className="project-detail-content">
        <Outlet />
      </div>
    </div>
  );
}
