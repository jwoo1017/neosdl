import PaneErrorBoundary from "./PaneErrorBoundary";
import ChatPane from "./ChatPane";
import DetailPane from "./DetailPane";
import WelcomePane from "./WelcomePane";
import FlowPane from "./FlowPane";

// flexlayout <Layout factory> — 탭 노드의 component 문자열을 실제 패널
// 컴포넌트로 매핑한다. (라우트 기반이던 기존 탭 시스템엔 이런 레지스트리가
// 없었으므로 여기서 새로 만든다.)
export function makePaneFactory({ updateNodeConfig, openDetailTab }) {
  return function factory(node) {
    const component = node.getComponent();
    const config = node.getConfig() || {};
    const { projectId } = config;

    let content;
    if (component === "chat") {
      content = <ChatPane projectId={projectId} />;
    } else if (component === "detail") {
      content = (
        <DetailPane
          projectId={projectId}
          initialPath={config.detailPath}
          navRequest={config.navRequest}
          onPathChange={(path) => updateNodeConfig(node.getId(), { detailPath: path })}
        />
      );
    } else if (component === "flow") {
      content = (
        <FlowPane
          key={config.flowNonce}
          projectId={projectId}
          scanId={config.scanId}
          flow={config.flow}
          onOpenInMap={(subPath) => openDetailTab({ id: projectId, name: config.projectName }, subPath)}
        />
      );
    } else if (component === "welcome") {
      content = <WelcomePane />;
    } else {
      content = <div className="pane-error">알 수 없는 패널: {component}</div>;
    }

    return (
      <PaneErrorBoundary key={`${component}:${projectId}`}>
        <div className="pane-content">{content}</div>
      </PaneErrorBoundary>
    );
  };
}
