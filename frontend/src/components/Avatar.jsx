import { useId, useState } from "react";
import RoleHatIcon from "../icons/RoleHatIcon";
import { ROLE_LABEL, STATUS_META } from "./avatarMeta";

// 공용 프로필 아바타(SVG). 사진이 있으면 원형으로 자르고, 없거나 못 불러오면
// 이름 첫 글자 + 사람마다 고정된 색(아이디 해시)으로 그린다.
//  - status: "active" | "idle" | "offline" — 오른쪽 아래 상태 점
//  - role:   "admin" | "member"             — 머리 위 역할 모자(RoleHatIcon)
// profile 은 백엔드 app/profiles.py 의 공개 프로필 모양: {id, nickname, name, avatar_url}.

const PALETTE = ["#2f9c8f", "#2563eb", "#7c3aed", "#db2777", "#ea580c", "#16a34a", "#0891b2", "#9333ea"];

function colorFor(id = "") {
  let h = 0;
  for (let i = 0; i < id.length; i += 1) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length];
}

function initialOf(profile) {
  const source = (profile?.name || profile?.nickname || "?").trim();
  return Array.from(source)[0]?.toUpperCase() || "?"; // 한글/이모지도 한 글자로
}

export default function Avatar({ profile, size = 32, status, role, className = "", title }) {
  const clipId = useId();
  const [broken, setBroken] = useState(null); // 실패한 URL — URL 이 바뀌면 다시 시도
  const url = profile?.avatar_url;
  const showImage = url && broken !== url;

  const tooltip = title ?? [
    profile ? `${profile.nickname}${profile.name && profile.name !== profile.nickname ? ` (${profile.name})` : ""}` : "알 수 없음",
    role && ROLE_LABEL[role],
    status && STATUS_META[status]?.label,
  ].filter(Boolean).join(" · ");

  return (
    <span className={`avatar ${role ? "avatar-has-hat" : ""} ${className}`} style={{ width: size, height: size }} title={tooltip}>
      <svg width={size} height={size} viewBox="0 0 40 40" role="img" aria-label={tooltip}>
        <defs>
          <clipPath id={clipId}>
            <circle cx="20" cy="20" r="20" />
          </clipPath>
        </defs>
        {showImage ? (
          <image
            href={url}
            width="40"
            height="40"
            preserveAspectRatio="xMidYMid slice"
            clipPath={`url(#${clipId})`}
            onError={() => setBroken(url)}
          />
        ) : (
          <>
            <circle cx="20" cy="20" r="20" fill={profile ? colorFor(profile.id) : "#9ca3af"} />
            <text
              x="20"
              y="21"
              textAnchor="middle"
              dominantBaseline="central"
              fontSize="18"
              fontWeight="600"
              fill="#ffffff"
            >
              {profile ? initialOf(profile) : "?"}
            </text>
          </>
        )}
        {status && (
          <circle
            cx="33.5"
            cy="33.5"
            r="6"
            fill={STATUS_META[status]?.color || STATUS_META.offline.color}
            stroke="var(--avatar-ring, #ffffff)"
            strokeWidth="2.5"
          />
        )}
      </svg>
      {role && <RoleHatIcon role={role} size={Math.max(12, Math.round(size * 0.55))} className="avatar-hat" />}
    </span>
  );
}
