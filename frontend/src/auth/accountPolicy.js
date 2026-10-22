// 가입·계정 정책의 화면 쪽 짝(app/signup_policy.py 와 같은 규칙·같은 오류 코드).
// 서버가 최종 판단하고, 여기서는 입력하는 동안 미리 보여주기만 한다.

export const MIN_PASSWORD_LENGTH = 10;

// 비밀번호 체크리스트 — [{key, label, ok}]
export function passwordChecks(password, email) {
  const pw = password || "";
  const kinds = [/[A-Za-z]/, /[0-9]/, /[^A-Za-z0-9\s]/].filter((re) => re.test(pw)).length;
  const local = (email || "").split("@")[0].toLowerCase();
  const bytes = new TextEncoder().encode(pw).length;
  return [
    { key: "length", label: `${MIN_PASSWORD_LENGTH}자 이상`, ok: pw.length >= MIN_PASSWORD_LENGTH && bytes <= 72 },
    { key: "kinds", label: "영문·숫자·특수문자 중 2종류 이상", ok: kinds >= 2 },
    { key: "email", label: "이메일 아이디를 포함하지 않기", ok: !(local.length >= 3 && pw.toLowerCase().includes(local)) },
  ];
}

export function passwordOk(password, email) {
  return passwordChecks(password, email).every((c) => c.ok);
}

const WEAK_REASONS = {
  too_short: `${MIN_PASSWORD_LENGTH}자 이상이어야 해요`,
  too_long: "너무 길어요(한글 기준 약 24자 이하)",
  needs_two_kinds: "영문·숫자·특수문자 중 2종류 이상을 섞어 주세요",
  contains_email: "이메일 아이디가 들어가 있어요",
  too_common: "너무 흔한 비밀번호예요",
};

const MESSAGES = {
  "invalid email or password": "이메일 또는 비밀번호가 올바르지 않아요.",
  "email already registered": "이미 가입된 이메일이에요. 로그인하거나 비밀번호 찾기를 이용하세요.",
  signup_not_allowed:
    "회사 이메일(@aeonlab.kr, @finble.io, @zerokleek.com)로 가입하거나, 그 주소로 초대를 받아야 가입할 수 있어요.",
  privacy_agreement_required: "개인정보 수집·이용에 동의해야 가입할 수 있어요.",
  invalid_name: "이름을 1~50자로 입력해 주세요.",
  invalid_nickname: "닉네임을 1~30자로 입력해 주세요.",
  account_suspended: "관리자가 비활성화한 계정이에요. 관리자에게 문의하세요.",
  github_signup_not_allowed:
    "GitHub 계정에 인증된 회사 이메일이 없어요. 회사 이메일로 가입한 뒤 마이페이지에서 GitHub를 연결하세요.",
  github_email_in_use:
    "그 회사 이메일로 가입한 계정이 이미 있어요. 이메일로 로그인한 뒤 마이페이지에서 GitHub를 연결하세요.",
  code_expired: "인증 코드가 만료됐어요. 코드를 다시 받아 주세요.",
  too_many_attempts: "시도 횟수를 넘었어요. 코드를 다시 받아 주세요.",
  guest_cannot_create_workspace: "게스트 계정은 워크스페이스를 만들 수 없어요. 초대받은 공간을 이용하세요.",
  cannot_suspend_self: "자기 자신은 비활성화할 수 없어요.",
  system_admin_required: "시스템 관리자만 할 수 있어요.",
};

function formatWait(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  const m = Math.ceil(s / 60);
  return s >= 60 ? `${m}분` : `${s}초`;
}

// 서버 detail 코드 → 화면 문구. 모르는 코드는 null(호출부가 기본 문구를 쓴다).
export function describeAccountError(detail) {
  if (typeof detail !== "string") return null;
  const [kind, value] = detail.split(":");
  if (kind === "account_locked") {
    return `로그인을 5번 연속 실패해서 잠겼어요. ${formatWait(value)} 뒤에 다시 시도하거나 비밀번호 찾기를 이용하세요.`;
  }
  if (kind === "weak_password") {
    const reasons = (value || "").split(",").map((r) => WEAK_REASONS[r]).filter(Boolean);
    return `비밀번호 규칙에 맞지 않아요: ${reasons.join(", ") || "다시 확인해 주세요"}.`;
  }
  if (kind === "invalid_code") return `인증 코드가 올바르지 않아요. (남은 시도 ${value}회)`;
  if (kind === "resend_cooldown") return `${value}초 후에 다시 요청할 수 있어요.`;
  return MESSAGES[detail] || null;
}
