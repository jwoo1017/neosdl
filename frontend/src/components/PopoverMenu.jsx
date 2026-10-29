import { useEffect, useLayoutEffect, useRef, useState } from "react";

// 버튼 옆에 뜨는 작은 동작 메뉴 — 모달 없이 바로 고른다.
// 사이드바처럼 overflow 로 잘리는 컨테이너 안에서도 보이도록 position: fixed 로
// anchor(DOMRect) 기준에 띄운다. 바깥 클릭·Escape·스크롤로 닫힌다.
// items: [{ key, label, hint?, onSelect }]
export default function PopoverMenu({ anchor, items, onClose, ariaLabel }) {
  const ref = useRef(null);
  const [pos, setPos] = useState({ top: anchor.bottom + 4, left: anchor.left });
  const [cursor, setCursor] = useState(0);

  // 화면 아래/오른쪽으로 넘치면 위/왼쪽으로 붙인다.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const { width, height } = el.getBoundingClientRect();
    let top = anchor.bottom + 4;
    let left = anchor.left;
    if (top + height > window.innerHeight - 8) top = Math.max(8, anchor.top - height - 4);
    if (left + width > window.innerWidth - 8) left = Math.max(8, anchor.right - width);
    setPos({ top, left });
  }, [anchor]);

  useEffect(() => {
    ref.current?.focus();
    const onDown = (e) => {
      if (!ref.current?.contains(e.target)) onClose();
    };
    const onScroll = () => onClose();
    document.addEventListener("mousedown", onDown);
    window.addEventListener("resize", onScroll);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("resize", onScroll);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, [onClose]);

  const pick = (item) => {
    onClose();
    item.onSelect();
  };

  const onKeyDown = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setCursor((c) => (c + step + items.length) % items.length);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (items[cursor]) pick(items[cursor]);
    }
  };

  return (
    <ul ref={ref} role="menu" aria-label={ariaLabel} tabIndex={-1} className="ui-popover-menu"
        style={{ top: pos.top, left: pos.left }} onKeyDown={onKeyDown}>
      {items.map((item, i) => (
        <li key={item.key} role="menuitem"
            className={`ui-popover-item${i === cursor ? " is-cursor" : ""}`}
            onMouseEnter={() => setCursor(i)}
            onClick={() => pick(item)}>
          <span className="ui-popover-label">{item.label}</span>
          {item.hint && <span className="ui-popover-hint">{item.hint}</span>}
        </li>
      ))}
    </ul>
  );
}
