import { useNavigate } from "react-router-dom";
import { useWorkspace } from "../WorkspaceContext";
import WorkspaceCreateForm from "../components/WorkspaceCreateForm";

export default function OnboardingPage() {
  const navigate = useNavigate();
  const { setActiveWorkspaceId, reload } = useWorkspace();

  const handleCreated = async (workspace) => {
    await reload();
    setActiveWorkspaceId(workspace.id);
    navigate("/workspace");
  };

  return (
    <div className="auth-page">
      <div className="auth-card onboarding-card">
        <h1>워크스페이스를 만들어보세요</h1>
        <p>개인 또는 회사, 어떤 용도로 쓸지 먼저 골라주세요.</p>

        <WorkspaceCreateForm onCreated={handleCreated} />

        <p className="muted onboarding-hint">초대 링크를 받았다면, 새로 만들지 말고 그 링크를 열어 합류하세요.</p>
      </div>
    </div>
  );
}
