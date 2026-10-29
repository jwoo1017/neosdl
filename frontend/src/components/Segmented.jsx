// 드롭다운 대신 쓰는 선택 컨트롤 두 가지 — 고를 수 있는 값이 한눈에 다 보인다.
//  - Segmented:   한 줄 버튼 묶음(역할·직무처럼 짧은 값). allowDeselect 면 선택된 걸 다시 눌러 해제("미지정").
//  - ChoiceCards: 아이콘 + 제목 + 설명 카드(공개 범위처럼 의미 설명이 필요한 값).
// 둘 다 radiogroup — 화살표 키로 이동하면 바로 선택된다.

function useArrowKeys(options, value, onChange, disabled) {
  return (e) => {
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    if (!step || disabled) return;
    e.preventDefault();
    const i = Math.max(0, options.findIndex((o) => o.value === value));
    const next = options[(i + step + options.length) % options.length];
    onChange(next.value);
    e.currentTarget.querySelector(`[data-value="${next.value}"]`)?.focus();
  };
}

export function Segmented({ options, value, onChange, ariaLabel, size = "md", allowDeselect = false, disabled = false }) {
  const onKeyDown = useArrowKeys(options, value, onChange, disabled);
  const focusValue = options.some((o) => o.value === value) ? value : options[0]?.value;
  return (
    <div role="radiogroup" aria-label={ariaLabel} className={`seg seg-${size}`} onKeyDown={onKeyDown}>
      {options.map(({ value: v, label, Icon }) => {
        const on = v === value;
        return (
          <button key={v} type="button" role="radio" aria-checked={on} data-value={v}
                  tabIndex={v === focusValue ? 0 : -1} disabled={disabled}
                  className={`seg-btn${on ? " is-on" : ""}`}
                  onClick={() => onChange(on && allowDeselect ? "" : v)}>
            {Icon && <Icon size={size === "sm" ? 13 : 14} />}
            <span>{label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function ChoiceCards({ options, value, onChange, ariaLabel, disabled = false }) {
  const onKeyDown = useArrowKeys(options, value, onChange, disabled);
  return (
    <div role="radiogroup" aria-label={ariaLabel} className="choice-cards" onKeyDown={onKeyDown}>
      {options.map(({ value: v, title, desc, Icon }) => {
        const on = v === value;
        return (
          <button key={v} type="button" role="radio" aria-checked={on} data-value={v}
                  tabIndex={on ? 0 : -1} disabled={disabled}
                  className={`choice-card${on ? " is-on" : ""}`} onClick={() => onChange(v)}>
            <span className="choice-card-top">
              <span className="choice-card-icon"><Icon size={18} /></span>
              <span className={`choice-card-dot${on ? " is-on" : ""}`} />
            </span>
            <span className="choice-card-title">{title}</span>
            <span className="choice-card-desc">{desc}</span>
          </button>
        );
      })}
    </div>
  );
}
