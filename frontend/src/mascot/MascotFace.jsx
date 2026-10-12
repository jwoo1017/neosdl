import blinkSvg from "./assets/blink-face.svg?raw";
import thinkingSvg from "./assets/thinking-face.svg?raw";

// 두 SVG는 자체 <style>/@keyframes를 내장한 완성된 애니메이션이라 별도
// 애니메이션 라이브러리가 필요 없다. 다만 .eye/.brow 등 클래스명이 겹치므로
// 한 번에 하나만 DOM에 마운트한다(교체 렌더링) — 그래야 선택자 충돌이 없다.
export default function MascotFace({ state }) {
  const svg = state === "thinking" ? thinkingSvg : blinkSvg;
  return <div className="mascot-face" dangerouslySetInnerHTML={{ __html: svg }} />;
}
