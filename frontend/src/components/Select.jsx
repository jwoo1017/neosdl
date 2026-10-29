import { useEffect, useId, useRef, useState } from "react";
import { CheckIcon, ChevronDownIcon } from "../icons/SpaceIcons";

// 네이티브 <select> 대체 드롭다운 — 브라우저 기본 목록 대신 직접 그린 listbox.
// options: [{ value, label }] · 키보드: ↑↓ 이동, Enter/Space 선택, Escape 닫기.
export default function Select({ value, options, onChange, disabled, size = "md", className = "", ariaLabel, placeholder }) {
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const rootRef = useRef(null);
  const listId = useId();
  const selected = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (!rootRef.current?.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const openList = () => {
    if (disabled) return;
    setCursor(Math.max(0, options.findIndex((o) => o.value === value)));
    setOpen(true);
  };

  const pick = (v) => {
    setOpen(false);
    if (v !== value) onChange(v);
  };

  const onKeyDown = (e) => {
    if (disabled) return;
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        openList();
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation(); // 드롭다운만 닫고 바깥 모달(document keydown)은 그대로
      setOpen(false);
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setCursor((c) => (c + step + options.length) % options.length);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (options[cursor]) pick(options[cursor].value);
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  };

  return (
    <div ref={rootRef} className={`ui-select ui-select-${size}${open ? " is-open" : ""} ${className}`}>
      <button type="button" className="ui-select-trigger" disabled={disabled} aria-label={ariaLabel}
              aria-haspopup="listbox" aria-expanded={open} aria-controls={listId}
              onClick={() => (open ? setOpen(false) : openList())} onKeyDown={onKeyDown}>
        <span className={`ui-select-value${selected ? "" : " is-placeholder"}`}>
          {selected ? selected.label : placeholder || "선택"}
        </span>
        <ChevronDownIcon size={14} className="ui-select-caret" />
      </button>
      {open && (
        <ul id={listId} role="listbox" className="ui-select-list">
          {options.map((o, i) => (
            <li key={o.value} role="option" aria-selected={o.value === value}
                className={`ui-select-option${i === cursor ? " is-cursor" : ""}${o.value === value ? " is-selected" : ""}`}
                onMouseEnter={() => setCursor(i)}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(o.value)}>
              <span className="ui-select-option-label">{o.label}</span>
              {o.value === value && <CheckIcon size={14} />}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
