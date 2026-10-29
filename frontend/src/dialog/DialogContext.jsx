import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import Modal from "../components/Modal";

// window.confirm / window.prompt 대체 — 브라우저 기본 창 대신 앱 Modal 로 묻는다.
// 사용: const dialog = useDialog();
//   if (!(await dialog.confirm({ title, message, confirmLabel, danger }))) return;
//   const name = await dialog.prompt({ title, label, defaultValue }); // 취소하면 null

const DialogContext = createContext(null);

function ConfirmDialog({ title, message, confirmLabel = "확인", cancelLabel = "취소", danger, onResult }) {
  return (
    <Modal title={title} onClose={() => onResult(false)}>
      {message && <p className="dialog-message">{message}</p>}
      <div className="mp-form-actions">
        <button type="button" className="mp-btn mp-btn-secondary" onClick={() => onResult(false)}>
          {cancelLabel}
        </button>
        <button type="button" autoFocus className={`mp-btn ${danger ? "mp-btn-danger" : "mp-btn-primary"}`}
                onClick={() => onResult(true)}>
          {confirmLabel}
        </button>
      </div>
    </Modal>
  );
}

function PromptDialog({ title, label, defaultValue = "", placeholder, confirmLabel = "저장", cancelLabel = "취소", onResult }) {
  const [value, setValue] = useState(defaultValue);
  const submit = (e) => {
    e.preventDefault();
    onResult(value);
  };
  return (
    <Modal title={title} onClose={() => onResult(null)}>
      <form onSubmit={submit}>
        {label && <p className="dialog-message">{label}</p>}
        <input className="dialog-input" autoFocus value={value} placeholder={placeholder}
               onChange={(e) => setValue(e.target.value)} onFocus={(e) => e.target.select()} />
        <div className="mp-form-actions">
          <button type="button" className="mp-btn mp-btn-secondary" onClick={() => onResult(null)}>
            {cancelLabel}
          </button>
          <button type="submit" className="mp-btn mp-btn-primary">{confirmLabel}</button>
        </div>
      </form>
    </Modal>
  );
}

export function DialogProvider({ children }) {
  const [current, setCurrent] = useState(null); // {kind, opts, id}
  const pending = useRef(null); // {resolve, cancelValue}
  const nextId = useRef(1);

  const open = useCallback((kind, opts) => new Promise((resolve) => {
    pending.current?.resolve(pending.current.cancelValue); // 이전 다이얼로그는 취소로 끝낸다
    pending.current = { resolve, cancelValue: kind === "confirm" ? false : null };
    setCurrent({ kind, opts, id: nextId.current++ });
  }), []);

  const finish = useCallback((result) => {
    pending.current?.resolve(result);
    pending.current = null;
    setCurrent(null);
  }, []);

  const value = useMemo(() => ({
    confirm: (opts) => open("confirm", typeof opts === "string" ? { title: "확인", message: opts } : opts),
    prompt: (opts) => open("prompt", opts),
  }), [open]);

  return (
    <DialogContext.Provider value={value}>
      {children}
      {current?.kind === "confirm" && <ConfirmDialog key={current.id} {...current.opts} onResult={finish} />}
      {current?.kind === "prompt" && <PromptDialog key={current.id} {...current.opts} onResult={finish} />}
    </DialogContext.Provider>
  );
}

export function useDialog() {
  return useContext(DialogContext);
}
