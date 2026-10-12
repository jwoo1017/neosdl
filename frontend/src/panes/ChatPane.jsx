import ProjectChatPage from "../pages/ProjectChatPage";

// 멀티페인 셸이 채팅 패널로 렌더하는 래퍼. ProjectChatPage는 projectId를
// prop으로 받으면 URL(useParams) 대신 그 값을 쓰므로, 서로 다른 프로젝트의
// 채팅을 여러 패널에 동시에 띄울 수 있다.
export default function ChatPane({ projectId }) {
  return <ProjectChatPage projectId={projectId} />;
}
