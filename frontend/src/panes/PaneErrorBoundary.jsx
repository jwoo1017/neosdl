import { Component } from "react";

// 한 패널에서 렌더 오류가 나도 나머지 패널/셸은 살아있게 한다.
export default class PaneErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div className="pane-error">
          <p>이 패널을 표시하는 중 오류가 발생했습니다.</p>
          <pre>{String(this.state.error?.message || this.state.error)}</pre>
          <button type="button" className="btn-primary" onClick={() => this.setState({ error: null })}>
            다시 시도
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
