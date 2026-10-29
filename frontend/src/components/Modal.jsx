import { useEffect, useRef } from "react";
import CloseIcon from "../icons/CloseIcon";

// 열린 모달 스택 — 모달 위에 확인 다이얼로그가 겹쳐 뜰 때 Escape 는 맨 위 하나만 닫는다.
const openStack = [];

export default function Modal({ title, onClose, children, wide = false }) {
  // onClose 가 렌더마다 새 함수여도 스택 순서가 흔들리지 않게 ref 로 들고, 등록은 마운트 때 한 번만.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const token = Symbol("modal");
    openStack.push(token);
    const onKeyDown = (e) => {
      if (e.key === "Escape" && openStack[openStack.length - 1] === token) onCloseRef.current();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      const i = openStack.indexOf(token);
      if (i >= 0) openStack.splice(i, 1);
    };
  }, []);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className={`modal-panel${wide ? " modal-panel-wide" : ""}`} onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{title}</h2>
          <button type="button" className="modal-close" onClick={onClose} aria-label="닫기">
            <CloseIcon />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
