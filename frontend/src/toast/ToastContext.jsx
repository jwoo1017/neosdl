import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AlertCircleIcon, CheckCircleIcon } from "../icons/AnalysisIcons";
import CloseIcon from "../icons/CloseIcon";

// 화면 오른쪽 아래에 쌓이는 알림. 7초 뒤 사라지고(마우스를 올리면 멈춤), 버튼 하나를 둘 수 있다.
// 사용: const toast = useToast(); toast.show({ kind: "success", title, body, action: { label, onClick } })

const ToastContext = createContext(null);
const DURATION_MS = 7000;

function ToastItem({ toast, onClose }) {
  const [paused, setPaused] = useState(false);
  const remainingRef = useRef(DURATION_MS);
  const startedRef = useRef(0);

  useEffect(() => {
    if (paused) return undefined;
    startedRef.current = Date.now();
    const timer = setTimeout(onClose, remainingRef.current);
    return () => {
      clearTimeout(timer);
      remainingRef.current -= Date.now() - startedRef.current;
    };
  }, [paused, onClose]);

  const Icon = toast.kind === "error" ? AlertCircleIcon : CheckCircleIcon;
  return (
    <div
      className={`toast toast-${toast.kind || "success"}`}
      role={toast.kind === "error" ? "alert" : "status"}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <span className="toast-icon"><Icon /></span>
      <div className="toast-text">
        <div className="toast-title">{toast.title}</div>
        {toast.body && <div className="toast-body">{toast.body}</div>}
        {toast.action && (
          <button
            type="button"
            className="toast-action"
            onClick={() => {
              toast.action.onClick();
              onClose();
            }}
          >
            {toast.action.label}
          </button>
        )}
      </div>
      <button type="button" className="toast-close" aria-label="알림 닫기" onClick={onClose}>
        <CloseIcon size={14} />
      </button>
    </div>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id) => setToasts((list) => list.filter((t) => t.id !== id)), []);
  const show = useCallback((toast) => {
    const id = nextId.current++;
    setToasts((list) => [...list.slice(-3), { ...toast, id }]); // 최대 4개
    return id;
  }, []);
  const value = useMemo(() => ({ show, dismiss }), [show, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite">
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onClose={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  return useContext(ToastContext) || { show: () => 0, dismiss: () => {} };
}
