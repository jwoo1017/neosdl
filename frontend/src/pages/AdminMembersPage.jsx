import { useCallback, useEffect, useState } from "react";
import { fetchAdminMembers, reactivateMember, suspendMember } from "../api";
import { describeAccountError } from "../auth/accountPolicy";
import Avatar from "../components/Avatar";
import Modal from "../components/Modal";
import { useMe } from "../MeContext";

const SEARCH_DEBOUNCE_MS = 250;

function formatDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getDate()).padStart(2, "0")}`;
}

function StatusBadges({ m }) {
  return (
    <span className="adm-badges">
      {m.suspended_at ? <span className="adm-badge adm-badge-off">비활성</span>
        : <span className="adm-badge adm-badge-on">활성</span>}
      {m.is_system_admin && <span className="adm-badge">관리자</span>}
      {m.is_guest && <span className="adm-badge adm-badge-guest">게스트</span>}
      {m.locked && <span className="adm-badge adm-badge-warn">로그인 잠김</span>}
      {!m.email_verified_at && <span className="adm-badge adm-badge-dim">이메일 미인증</span>}
    </span>
  );
}

// 시스템 관리자 전용 — 회원 검색, 비활성화(퇴사자 처리)/재활성화(app/admin.py).
export default function AdminMembersPage() {
  const { me } = useMe();
  const [query, setQuery] = useState("");
  const [members, setMembers] = useState(null);
  const [error, setError] = useState(null);
  const [confirmTarget, setConfirmTarget] = useState(null); // {member, action}
  const [busy, setBusy] = useState(false);

  const load = useCallback((q) => {
    fetchAdminMembers(q)
      .then((rows) => {
        setMembers(rows);
        setError(null);
      })
      .catch((err) => setError(describeAccountError(err.detail) || err.message));
  }, []);

  useEffect(() => {
    if (!me?.is_system_admin) return undefined;
    const t = setTimeout(() => load(query.trim()), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [query, load, me?.is_system_admin]);

  if (!me?.is_system_admin) {
    return (
      <div className="adm-page">
        <h1 className="adm-title">회원 관리</h1>
        <p className="muted">시스템 관리자만 볼 수 있어요.</p>
      </div>
    );
  }

  const apply = async () => {
    const { member, action } = confirmTarget;
    setBusy(true);
    try {
      await (action === "suspend" ? suspendMember(member.id) : reactivateMember(member.id));
      setConfirmTarget(null);
      load(query.trim());
    } catch (err) {
      setError(describeAccountError(err.detail) || err.message);
      setConfirmTarget(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="adm-page">
      <div className="adm-head">
        <div>
          <h1 className="adm-title">회원 관리</h1>
          <p className="adm-desc">
            퇴사·계약 종료 등으로 접근을 막아야 하는 계정을 비활성화하세요. 비활성화하면 즉시 모든 기기에서 로그아웃되고,
            다시 로그인할 수 없어요. 작성한 기록과 소속은 그대로 남아요.
          </p>
        </div>
        <input
          className="adm-search"
          type="search"
          placeholder="이름·닉네임·이메일 검색"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      {error && <div className="alert-error">{error}</div>}

      {members === null ? (
        <p className="muted">불러오는 중...</p>
      ) : members.length === 0 ? (
        <p className="muted">검색 결과가 없어요.</p>
      ) : (
        <div className="adm-table-wrap">
          <table className="adm-table">
            <thead>
              <tr>
                <th>회원</th>
                <th>상태</th>
                <th>가입일</th>
                <th>최근 로그인</th>
                <th aria-label="작업" />
              </tr>
            </thead>
            <tbody>
              {members.map((m) => (
                <tr key={m.id} className={m.suspended_at ? "adm-row-off" : ""}>
                  <td>
                    <div className="adm-member">
                      <Avatar profile={m} size={30} />
                      <div className="adm-member-text">
                        <span className="adm-member-name">{m.name} <span className="muted">· {m.nickname}</span></span>
                        <span className="adm-member-email">{m.email || "이메일 없음"}</span>
                      </div>
                    </div>
                  </td>
                  <td><StatusBadges m={m} /></td>
                  <td className="adm-date">{formatDate(m.created_at)}</td>
                  <td className="adm-date">{formatDate(m.last_login_at)}</td>
                  <td className="adm-action">
                    {m.id === me.id ? (
                      <span className="muted">나</span>
                    ) : m.suspended_at ? (
                      <button type="button" className="mp-btn mp-btn-secondary"
                              onClick={() => setConfirmTarget({ member: m, action: "reactivate" })}>
                        다시 활성화
                      </button>
                    ) : (
                      <button type="button" className="mp-btn mp-btn-danger"
                              onClick={() => setConfirmTarget({ member: m, action: "suspend" })}>
                        비활성화
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {confirmTarget && (
        <Modal
          title={confirmTarget.action === "suspend" ? "계정 비활성화" : "계정 다시 활성화"}
          onClose={() => !busy && setConfirmTarget(null)}
        >
          <p className="adm-confirm-text">
            <strong>{confirmTarget.member.name}</strong> ({confirmTarget.member.email})
            {confirmTarget.action === "suspend"
              ? " 계정을 비활성화할까요? 지금 열려 있는 모든 세션이 끊기고, 다시 활성화하기 전까지 로그인할 수 없어요."
              : " 계정을 다시 활성화할까요? 다시 로그인할 수 있게 돼요."}
          </p>
          <div className="mp-form-actions">
            <button type="button" className="mp-btn mp-btn-secondary" disabled={busy} onClick={() => setConfirmTarget(null)}>
              취소
            </button>
            <button
              type="button"
              className={`mp-btn ${confirmTarget.action === "suspend" ? "mp-btn-danger" : "mp-btn-primary"}`}
              disabled={busy}
              onClick={apply}
            >
              {busy ? "처리 중..." : confirmTarget.action === "suspend" ? "비활성화" : "다시 활성화"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}
