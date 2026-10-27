// 프론트/백엔드는 같은 오리진으로 묶여 있다 — 운영은 nginx가, 로컬 개발은
// Vite dev 프록시(vite.config.js의 /api)가 백엔드로 넘긴다. 그래서
// API_BASE는 항상 "지금 떠 있는 바로 이 페이지의 오리진"이고(이전처럼
// localhost:8000을 따로 하드코딩하지 않는다), 모든 앱 라우트는 /api
// 아래(app/main.py)에 있다 — /config만 예외로 루트에 남아있다.
const API_BASE = window.location.origin;

async function apiFetch(path, options = {}) {
  const res = await fetch(`${API_BASE}/api${path}`, { ...options, credentials: "include" });
  if (res.status === 401) {
    // 세션 만료 — 로그인 방식이 이메일/비번일 수도 있어서 GitHub로 바로
    // 보내지 않고 우리 로그인 페이지로 보낸다.
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    return new Promise(() => {}); // 리다이렉트 진행 중이니 이 호출은 그대로 매달아 둔다
  }
  if (res.status === 403) {
    // 이메일 미인증 계정은 백엔드가 기본 차단한다(app/auth.py) — 인증 화면으로 보낸다.
    // 다른 403(권한 부족)은 호출부가 그대로 처리하도록 본문만 엿보고 res 는 돌려준다.
    const detail = (await res.clone().json().catch(() => ({}))).detail;
    if (detail === "email_not_verified") {
      window.location.href = `/verify-email?next=${encodeURIComponent(window.location.pathname)}`;
      return new Promise(() => {});
    }
  }
  return res;
}

// 실패 응답의 detail(에러 코드)을 Error 에 실어 던진다 — 화면이 spaceRoles.describeSpaceError 로 문구를 고른다.
async function throwWithDetail(res, fallback) {
  const raw = (await res.json().catch(() => ({}))).detail;
  const detail = typeof raw === "string" ? raw : null;
  throw Object.assign(new Error(detail || `${fallback} failed: ${res.status}`), { status: res.status, detail });
}

export async function fetchConfig() {
  const res = await fetch(`${API_BASE}/config`);
  if (!res.ok) throw new Error(`GET /config failed: ${res.status}`);
  return res.json();
}

// 채팅 모델 드롭다운 목록 — LiteLLM 게이트웨이가 서빙 중인 모델에서 나온다.
// { default: "<별칭>", models: [{id,label,provider,group,starred,available}] }
export async function fetchModels() {
  const res = await apiFetch("/models");
  if (!res.ok) throw new Error(`GET /models failed: ${res.status}`);
  return res.json();
}

export function githubLoginUrl(next) {
  const url = new URL(`${API_BASE}/api/auth/github/login`);
  if (next) url.searchParams.set("next", next);
  return url.toString();
}

export function githubAppInstallUrl(appSlug, state) {
  const url = new URL(`https://github.com/apps/${appSlug}/installations/new`);
  if (state) url.searchParams.set("state", state);
  return url.toString();
}

// ---------------------------------------------------------------- auth
// 로그인 전 인증 API 공통 — 실패 시 Error 에 `status`/`detail` 을 실어 던진다
// (화면이 auth/accountPolicy.js describeAccountError 로 사유별 문구를 고른다).
async function authPost(path, body) {
  const res = await fetch(`${API_BASE}/api${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const raw = (await res.json().catch(() => ({}))).detail;
    const detail = typeof raw === "string" ? raw : `${path} failed: ${res.status}`;
    throw Object.assign(new Error(detail), { status: res.status, detail });
  }
  return res.status === 204 ? null : res.json();
}

export function signup(email, password, name, nickname, privacyAgreed) {
  // { email_verification_required, sent, expires_in, resend_after }
  return authPost("/auth/signup", { email, password, name, nickname, privacy_agreed: privacyAgreed });
}

// 비밀번호 찾기 — 계정 유무와 관계없이 같은 응답(가입 여부를 드러내지 않는다).
export function requestPasswordReset(email) {
  return authPost("/auth/password-reset/request", { email });
}

export function confirmPasswordReset(email, code, newPassword) {
  return authPost("/auth/password-reset/confirm", { email, code, new_password: newPassword });
}

// 이메일 인증 코드 — 실패 시 Error 에 `status`/`detail` 을 실어 던진다(화면이 사유별 문구를 고름).
async function verificationCall(path, body) {
  const res = await fetch(`${API_BASE}/api/auth/email-verification/${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || `${path} failed: ${res.status}`;
    throw Object.assign(new Error(detail), { status: res.status, detail });
  }
  return res.status === 204 ? null : res.json();
}

export function sendVerificationCode() {
  return verificationCall("send"); // { sent, expires_in, resend_after }
}

export function verifyEmailCode(code) {
  return verificationCall("verify", { code });
}

export function login(email, password) {
  return authPost("/auth/login", { email, password });
}

export async function fetchMe() {
  const res = await apiFetch("/me");
  if (!res.ok) throw new Error(`GET /me failed: ${res.status}`);
  return res.json();
}

export async function logout() {
  await apiFetch("/auth/logout", { method: "POST" });
}

export async function fetchGithubAccounts() {
  const res = await apiFetch("/me/github-accounts");
  if (!res.ok) throw new Error(`GET /me/github-accounts failed: ${res.status}`);
  return res.json();
}

export async function unlinkGithubAccount(githubAccountId) {
  const res = await apiFetch(`/me/github-accounts/${githubAccountId}`, { method: "DELETE" });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `unlink failed: ${res.status}`);
}

// ---------------------------------------------------------------- 마이페이지 (app/me.py, app/avatars.py)
// 실패 시 Error 에 status/detail 을 실어 던진다 — 화면이 사유별 문구를 고른다.
async function meCall(path, { method = "POST", body, form } = {}) {
  const res = await apiFetch(path, {
    method,
    headers: form ? undefined : { "content-type": "application/json" },
    body: form || (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail || `${method} ${path} failed: ${res.status}`;
    throw Object.assign(new Error(typeof detail === "string" ? detail : detail.code || "error"),
                        { status: res.status, detail });
  }
  return res.status === 204 ? null : res.json();
}

export const updateProfile = (fields) => meCall("/me", { method: "PATCH", body: fields });
export const changePassword = (currentPassword, newPassword) =>
  meCall("/me/password", { body: { current_password: currentPassword || null, new_password: newPassword } });
export const requestEmailChange = (newEmail, password) =>
  meCall("/me/email", { body: { new_email: newEmail, password: password || null } });
export const confirmEmailChange = (code) => meCall("/me/email/verify", { body: { code } });
// 개인정보 수집·이용 동의(GitHub 가입·정책 도입 전 가입 계정이 첫 화면에서)
export const agreePrivacy = () => meCall("/me/privacy-agreement");

// 시스템 관리자 — 회원 비활성화(퇴사자 처리)/재활성화. app/admin.py
export const fetchAdminMembers = (q = "") =>
  meCall(`/admin/members${q ? `?q=${encodeURIComponent(q)}` : ""}`, { method: "GET" });
export const suspendMember = (memberId) => meCall(`/admin/members/${memberId}/suspend`);
export const reactivateMember = (memberId) => meCall(`/admin/members/${memberId}/reactivate`);
export const withdrawAccount = (password, confirm) =>
  meCall("/me", { method: "DELETE", body: { password: password || null, confirm } });

export function uploadAvatar(file) {
  const form = new FormData();
  form.append("file", file);
  return meCall("/me/avatar", { form });
}
export const deleteAvatar = () => meCall("/me/avatar", { method: "DELETE" });
export const setAvatarSource = (source) => meCall("/me/avatar-source", { method: "PATCH", body: { source } });

// ---------------------------------------------------------------- 초대 수신함
export async function fetchMyInvites() {
  const res = await apiFetch("/me/invites");
  if (!res.ok) throw new Error(`GET /me/invites failed: ${res.status}`);
  return res.json();
}

export async function acceptMyInvite(kind, inviteId) {
  const res = await apiFetch(`/me/invites/${kind}/${inviteId}/accept`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /me/invites/${kind}/${inviteId}/accept failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- installations
export async function fetchMyInstallations() {
  const res = await apiFetch("/me/installations");
  if (!res.ok) throw new Error(`GET /me/installations failed: ${res.status}`);
  return res.json();
}

export async function fetchInstallationRepositories(installationId) {
  const res = await apiFetch(`/installations/${installationId}/repositories`);
  if (!res.ok) throw new Error(`GET /installations/${installationId}/repositories failed: ${res.status}`);
  return res.json();
}

export async function registerRepository(installationId, repo) {
  const res = await apiFetch("/repositories", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      installation_id: installationId,
      provider: "github",
      remote_id: repo.remote_id,
      namespace: repo.namespace,
      name: repo.name,
      clone_url: repo.clone_url,
      default_branch: repo.default_branch,
    }),
  });
  if (!res.ok) throw new Error(`POST /repositories failed: ${res.status}`);
  return res.json();
}

export async function updateRepositoryWebhook(repositoryId, enabled) {
  const res = await apiFetch(`/repositories/${repositoryId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ webhook_ci_enabled: enabled }),
  });
  if (!res.ok) throw new Error(`PATCH /repositories/${repositoryId} failed: ${res.status}`);
  return res.json();
}

// 마스코트 사이드바의 "PR 생성" 액션 — 저장소별 2차 동의 게이트(sql/026).
// webhook_ci_enabled는 백엔드에서 필수 필드라 현재 값을 그대로 실어 보낸다.
export async function updateRepositoryPrFix(repositoryId, webhookEnabled, prFixEnabled) {
  const res = await apiFetch(`/repositories/${repositoryId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ webhook_ci_enabled: webhookEnabled, pr_fix_enabled: prFixEnabled }),
  });
  if (!res.ok) throw new Error(`PATCH /repositories/${repositoryId} failed: ${res.status}`);
  return res.json();
}

export async function requestScan(repositoryId, overrides = {}) {
  const res = await apiFetch(`/repositories/${repositoryId}/scans`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(overrides),
  });
  if (!res.ok) throw new Error(`POST /repositories/${repositoryId}/scans failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- repository: branches/files/commits/PRs
export async function fetchBranches(repositoryId) {
  const res = await apiFetch(`/repositories/${repositoryId}/branches`);
  if (!res.ok) throw new Error(`GET /repositories/${repositoryId}/branches failed: ${res.status}`);
  return res.json();
}

export async function fetchRepoTree(repositoryId, path = "", ref = "") {
  const url = new URL(`${API_BASE}/repositories/${repositoryId}/tree`);
  if (path) url.searchParams.set("path", path);
  if (ref) url.searchParams.set("ref", ref);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /repositories/${repositoryId}/tree failed: ${res.status}`);
  return res.json();
}

export async function fetchRepoFileContent(repositoryId, path, ref = "") {
  const url = new URL(`${API_BASE}/repositories/${repositoryId}/contents`);
  url.searchParams.set("path", path);
  if (ref) url.searchParams.set("ref", ref);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /repositories/${repositoryId}/contents failed: ${res.status}`);
  return res.json();
}

export async function fetchRepositoryCommits(repositoryId, branch = "", limit = 100) {
  const url = new URL(`${API_BASE}/repositories/${repositoryId}/commits`);
  if (branch) url.searchParams.set("branch", branch);
  url.searchParams.set("limit", limit);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /repositories/${repositoryId}/commits failed: ${res.status}`);
  return res.json();
}

export async function fetchCommitFiles(repositoryId, sha) {
  const res = await apiFetch(`/repositories/${repositoryId}/commits/${sha}/files`);
  if (!res.ok) throw new Error(`GET /repositories/${repositoryId}/commits/${sha}/files failed: ${res.status}`);
  return res.json();
}

export async function fetchPullRequests(repositoryId, state = "") {
  const url = new URL(`${API_BASE}/repositories/${repositoryId}/pull-requests`);
  if (state) url.searchParams.set("state", state);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /repositories/${repositoryId}/pull-requests failed: ${res.status}`);
  return res.json();
}

export async function syncPullRequests(repositoryId) {
  const res = await apiFetch(`/repositories/${repositoryId}/pull-requests/sync`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /repositories/${repositoryId}/pull-requests/sync failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- releases
export async function fetchReleases(repositoryId) {
  const res = await apiFetch(`/repositories/${repositoryId}/releases`);
  if (!res.ok) throw new Error(`GET /repositories/${repositoryId}/releases failed: ${res.status}`);
  return res.json();
}

export async function fetchRelease(repositoryId, releaseId) {
  const res = await apiFetch(`/repositories/${repositoryId}/releases/${releaseId}`);
  if (!res.ok) throw new Error(`GET /repositories/${repositoryId}/releases/${releaseId} failed: ${res.status}`);
  return res.json();
}

export async function syncReleases(repositoryId) {
  const res = await apiFetch(`/repositories/${repositoryId}/releases/sync`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /repositories/${repositoryId}/releases/sync failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- security analysis
export async function fetchSecurityFindings(scanId, includeWhitelisted = false) {
  const url = new URL(`${API_BASE}/scans/${scanId}/security/findings`);
  if (includeWhitelisted) url.searchParams.set("include_whitelisted", "true");
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /scans/${scanId}/security/findings failed: ${res.status}`);
  return res.json();
}

export async function fetchRiskHistory(repositoryId) {
  const res = await apiFetch(`/repositories/${repositoryId}/security/risk-history`);
  if (!res.ok) throw new Error(`GET /repositories/${repositoryId}/security/risk-history failed: ${res.status}`);
  return res.json();
}

// Security History (Phase 2) — 커밋/브랜치 축의 보안 상태 추적
export async function fetchSecurityTimeline(repositoryId, branch) {
  const url = new URL(`${API_BASE}/repositories/${repositoryId}/security/timeline`);
  if (branch) url.searchParams.set("branch", branch);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET security/timeline failed: ${res.status}`);
  return res.json(); // { branches, series }
}

export async function fetchSecurityDiff(repositoryId, headScan, baseScan) {
  const url = new URL(`${API_BASE}/repositories/${repositoryId}/security/diff`);
  url.searchParams.set("head_scan", headScan);
  if (baseScan) url.searchParams.set("base_scan", baseScan);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `GET security/diff failed: ${res.status}`);
  return res.json();
}

export async function fetchRiskIdentities(repositoryId, { status, branch } = {}) {
  const url = new URL(`${API_BASE}/repositories/${repositoryId}/security/risks`);
  if (status) url.searchParams.set("status", status);
  if (branch) url.searchParams.set("branch", branch);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET security/risks failed: ${res.status}`);
  return res.json(); // { summary, risks }
}

export async function fetchRiskIdentity(repositoryId, fingerprint) {
  const res = await apiFetch(`/repositories/${repositoryId}/security/risks/${fingerprint}`);
  if (!res.ok) throw new Error(`GET security/risks/${fingerprint} failed: ${res.status}`);
  return res.json(); // { identity, lifecycle, observations }
}

export async function explainRiskIdentity(repositoryId, fingerprint) {
  const res = await apiFetch(`/repositories/${repositoryId}/security/risks/${fingerprint}/explain`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`POST security/risks/${fingerprint}/explain failed: ${res.status}`);
  return res.json(); // { explanation }
}

export async function fetchWhitelist(projectId) {
  const res = await apiFetch(`/projects/${projectId}/security/whitelist`);
  if (!res.ok) throw new Error(`GET /projects/${projectId}/security/whitelist failed: ${res.status}`);
  return res.json();
}

export async function createWhitelistEntry(projectId, body) {
  const res = await apiFetch(`/projects/${projectId}/security/whitelist`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST /projects/${projectId}/security/whitelist failed: ${res.status}`);
  return res.json();
}

export async function deleteWhitelistEntry(projectId, whitelistId) {
  const res = await apiFetch(`/projects/${projectId}/security/whitelist/${whitelistId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE /projects/${projectId}/security/whitelist/${whitelistId} failed: ${res.status}`);
}

// ---------------------------------------------------------------- risk intelligence (Phase 2)
// GET /scans/{id}/risk — { status: "analyzing" } (백그라운드 잡 진행 중) 또는
// { status: "ready", summary, risk_objects: [...] }
export async function fetchScanRisk(scanId) {
  const res = await apiFetch(`/scans/${scanId}/risk`);
  if (!res.ok) throw new Error(`GET /scans/${scanId}/risk failed: ${res.status}`);
  return res.json();
}

// 심볼 하나의 Risk Object + evidence_detail + evidence_graph
export async function fetchRiskSymbol(scanId, symbolKey) {
  const url = new URL(`${API_BASE}/scans/${scanId}/risk/symbol`);
  url.searchParams.set("symbol_key", symbolKey);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /scans/${scanId}/risk/symbol failed: ${res.status}`);
  return res.json();
}

// Query-aware Contextual Risk — { query_intent, results: [{ risk_object, exposure_boundary, base_exposure }] }
export async function evaluateRisk(scanId, query, targets) {
  const res = await apiFetch(`/scans/${scanId}/risk/evaluate`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ query, targets }),
  });
  if (!res.ok) throw new Error(`POST /scans/${scanId}/risk/evaluate failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- AI settings / usage
export async function fetchProjectAiSettings(projectId) {
  const res = await apiFetch(`/projects/${projectId}/ai-settings`);
  if (!res.ok) throw new Error(`GET /projects/${projectId}/ai-settings failed: ${res.status}`);
  return res.json();
}

export async function updateProjectAiSettings(projectId, body) {
  const res = await apiFetch(`/projects/${projectId}/ai-settings`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`PATCH /projects/${projectId}/ai-settings failed: ${res.status}`);
  return res.json();
}

export async function fetchAiUsage(projectId) {
  const res = await apiFetch(`/projects/${projectId}/ai-usage`);
  if (!res.ok) throw new Error(`GET /projects/${projectId}/ai-usage failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- scans / graph
export async function fetchScans(projectId, repositoryId) {
  const url = new URL(`${API_BASE}/projects/${projectId}/scans`);
  if (repositoryId) url.searchParams.set("repository_id", repositoryId);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /projects/${projectId}/scans failed: ${res.status}`);
  return res.json();
}

export async function fetchScan(scanId) {
  const res = await apiFetch(`/scans/${scanId}`);
  if (!res.ok) throw new Error(`GET /scans/${scanId} failed: ${res.status}`);
  return res.json();
}

export async function fetchGraph(scanId, includeUnresolved = false, domain = "", view = "all") {
  const url = new URL(`${API_BASE}/scans/${scanId}/graph`);
  url.searchParams.set("include_unresolved", includeUnresolved ? "true" : "false");
  if (domain) url.searchParams.set("domain", domain);
  if (view) url.searchParams.set("view", view);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /scans/${scanId}/graph failed: ${res.status}`);
  return res.json(); // { nodes, edges, max_recent_changes }
}

export async function fetchDomainGraph(scanId) {
  const res = await apiFetch(`/scans/${scanId}/graph/domains`);
  if (!res.ok) throw new Error(`GET /scans/${scanId}/graph/domains failed: ${res.status}`);
  return res.json(); // { nodes, edges, max_file_count }
}

export async function fetchDomains(scanId) {
  const res = await apiFetch(`/scans/${scanId}/domains`);
  if (!res.ok) throw new Error(`GET /scans/${scanId}/domains failed: ${res.status}`);
  return res.json();
}

export async function fetchFunctions(scanId) {
  const res = await apiFetch(`/scans/${scanId}/functions`);
  if (!res.ok) throw new Error(`GET /scans/${scanId}/functions failed: ${res.status}`);
  return res.json(); // [{ symbol_key, qualified_name, path }]
}

export async function fetchFileContent(scanId, path) {
  const url = new URL(`${API_BASE}/scans/${scanId}/file`);
  url.searchParams.set("path", path);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /scans/${scanId}/file failed: ${res.status}`);
  return res.json(); // { path, lang, content }
}

export async function fetchDataflow(scanId, mode = "security", limit = 50) {
  const url = new URL(`${API_BASE}/scans/${scanId}/dataflow`);
  url.searchParams.set("mode", mode);
  url.searchParams.set("limit", limit);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /scans/${scanId}/dataflow failed: ${res.status}`);
  return res.json(); // { mode, findings }
}

export async function fetchFunctionGraph(scanId, symbolKey) {
  const url = new URL(`${API_BASE}/scans/${scanId}/function-graph`);
  url.searchParams.set("symbol_key", symbolKey);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /scans/${scanId}/function-graph failed: ${res.status}`);
  return res.json(); // { symbol_key, qualified_name, path, nodes, edges }
}

export async function searchSymbols(scanId, q, limit = 20) {
  const url = new URL(`${API_BASE}/scans/${scanId}/search`);
  url.searchParams.set("q", q);
  url.searchParams.set("limit", limit);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /scans/${scanId}/search failed: ${res.status}`);
  return res.json();
}

// 질문 처리 진행 상황(AgentEvent)과 답변 토큰을 SSE로 실시간 수신한다
// (app/ask_stream.py). POST 바디(질문)가 필요해서 EventSource는 못 쓰고
// fetch + ReadableStream으로 직접 프레임을 파싱한다 — "data: {...}\n\n"
// 단위로 잘라 type별 콜백에 넘긴다.
export async function streamAskQuestion(scanId, question, { model, onEvent, onChunk, onDone, onError, signal } = {}) {
  const res = await apiFetch(`/scans/${scanId}/ask`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(model ? { question, model } : { question }),
    signal,
  });
  if (!res.ok || !res.body) {
    const detail = (await res.json().catch(() => ({}))).detail || `POST /scans/${scanId}/ask failed: ${res.status}`;
    throw new Error(detail);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const payload = JSON.parse(line.slice(6));
      if (payload.type === "agent_event") onEvent?.(payload.event);
      else if (payload.type === "answer_chunk") onChunk?.(payload.delta);
      else if (payload.type === "done") onDone?.(payload);
      else if (payload.type === "error") onError?.(payload.error);
    }
  }
}

// ---------------------------------------------------------------- project chat
// 프로젝트 채팅은 세션 단위로 쪼개져 있다(sql/018, app/chat.py) — 세션 목록/
// 생성/이름변경/삭제는 REST, 메시지 히스토리도 세션별 GET, 질문 전송만 WS.
export async function fetchChatSessions(projectId) {
  const res = await apiFetch(`/projects/${projectId}/chat/sessions`);
  if (!res.ok) throw new Error(`GET /projects/${projectId}/chat/sessions failed: ${res.status}`);
  return res.json();
}

export async function createChatSession(projectId, title = null) {
  const res = await apiFetch(`/projects/${projectId}/chat/sessions`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`POST /projects/${projectId}/chat/sessions failed: ${res.status}`);
  return res.json();
}

export async function renameChatSession(projectId, sessionId, title) {
  const res = await apiFetch(`/projects/${projectId}/chat/sessions/${sessionId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) throw new Error(`PATCH chat session failed: ${res.status}`);
  return res.json();
}

export async function deleteChatSession(projectId, sessionId) {
  const res = await apiFetch(`/projects/${projectId}/chat/sessions/${sessionId}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) throw new Error(`DELETE chat session failed: ${res.status}`);
}

export async function fetchChatMessages(projectId, sessionId) {
  const res = await apiFetch(`/projects/${projectId}/chat/sessions/${sessionId}/messages`);
  if (!res.ok) throw new Error(`GET chat session messages failed: ${res.status}`);
  return res.json();
}

// 질문 전송은 REST가 아니라 WebSocket 하나로만 받는다(app/chat.py) — 실시간
// 스트리밍 + 다중 뷰어 브로드캐스트를 위해서다. 세션 쿠키는 same-site라
// WS 핸드셰이크에도 자동으로 실린다.
export function chatSocketUrl(projectId, sessionId) {
  return `${API_BASE.replace(/^http/, "ws")}/api/ws/projects/${projectId}/chat/sessions/${sessionId}`;
}

// ---------------------------------------------------------------- mascot / repo activity
// push/PR/스캔/수정 이벤트를 실시간으로 받는 저장소별 소켓 — app/repo_activity.py.
export function repoActivitySocketUrl(repositoryId) {
  return `${API_BASE.replace(/^http/, "ws")}/api/ws/repositories/${repositoryId}/activity`;
}

export async function fetchRepositoryActivityFeed(repositoryId, limit = 50) {
  const url = new URL(`${API_BASE}/repositories/${repositoryId}/activity/feed`);
  url.searchParams.set("limit", limit);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET activity/feed failed: ${res.status}`);
  return res.json();
}

export async function generateFix(findingId) {
  const res = await apiFetch(`/findings/${findingId}/generate-fix`, { method: "POST" });
  if (!res.ok) throw new Error(`POST generate-fix failed: ${res.status}`);
  return res.json();
}

export async function fetchFix(findingId) {
  const res = await apiFetch(`/findings/${findingId}/fix`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GET fix failed: ${res.status}`);
  return res.json();
}

export async function createFixPr(findingId) {
  const res = await apiFetch(`/findings/${findingId}/create-pr`, { method: "POST" });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `create-pr failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- engagements (자율 보안 검증 run)
export async function fetchEngagements(projectId) {
  const res = await apiFetch(`/projects/${projectId}/engagements`);
  if (!res.ok) throw new Error(`GET engagements failed: ${res.status}`);
  return res.json();
}

export async function createEngagement(projectId, body) {
  const res = await apiFetch(`/projects/${projectId}/engagements`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `create engagement failed: ${res.status}`);
  return res.json();
}

export async function fetchEngagement(engagementId) {
  const res = await apiFetch(`/engagements/${engagementId}`);
  if (!res.ok) throw new Error(`GET engagement failed: ${res.status}`);
  return res.json();
}

// 승인 대기(HITL) 중인 engagement 의 pending 명령을 승인하고 재개한다.
export async function approveEngagement(engagementId) {
  const res = await apiFetch(`/engagements/${engagementId}/approve`, { method: "POST" });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `approve failed: ${res.status}`);
  return res.json();
}

// engagement 진행 상태를 실시간으로 받는 읽기 전용 소켓 — app/engagements.py.
export function engagementSocketUrl(engagementId) {
  return `${API_BASE.replace(/^http/, "ws")}/api/ws/engagements/${engagementId}`;
}

// ---------------------------------------------------------------- workspaces
export async function fetchWorkspaces() {
  const res = await apiFetch("/workspaces");
  if (!res.ok) throw new Error(`GET /workspaces failed: ${res.status}`);
  return res.json();
}

export async function createWorkspace(name, type = "company") {
  const res = await apiFetch("/workspaces", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, type }),
  });
  if (!res.ok) throw new Error(`POST /workspaces failed: ${res.status}`);
  return res.json();
}

export async function fetchWorkspace(workspaceId) {
  const res = await apiFetch(`/workspaces/${workspaceId}`);
  if (!res.ok) throw new Error(`GET /workspaces/${workspaceId} failed: ${res.status}`);
  return res.json();
}

export async function updateWorkspace(workspaceId, name) {
  const res = await apiFetch(`/workspaces/${workspaceId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`PATCH /workspaces/${workspaceId} failed: ${res.status}`);
  return res.json();
}

export async function deleteWorkspace(workspaceId) {
  const res = await apiFetch(`/workspaces/${workspaceId}`, { method: "DELETE" });
  if (!res.ok) await throwWithDetail(res, `DELETE /workspaces/${workspaceId}`);
}

export async function updateWorkspaceMemberRole(workspaceId, memberId, role) {
  const res = await apiFetch(`/workspaces/${workspaceId}/members/${memberId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ role }),
  });
  if (!res.ok) await throwWithDetail(res, `PATCH /workspaces/${workspaceId}/members/${memberId}`);
  return res.json();
}

export async function removeWorkspaceMember(workspaceId, memberId) {
  const res = await apiFetch(`/workspaces/${workspaceId}/members/${memberId}`, { method: "DELETE" });
  if (!res.ok) await throwWithDetail(res, `DELETE /workspaces/${workspaceId}/members/${memberId}`);
}

export async function leaveWorkspace(workspaceId) {
  const res = await apiFetch(`/workspaces/${workspaceId}/leave`, { method: "POST" });
  if (!res.ok) await throwWithDetail(res, `POST /workspaces/${workspaceId}/leave`);
}

export async function createWorkspaceInvite(workspaceId, role, targetEmail) {
  const res = await apiFetch(`/workspaces/${workspaceId}/invites`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ role, target_email: targetEmail || null }),
  });
  if (!res.ok) {
    throw new Error((await res.json().catch(() => ({}))).detail || `POST /workspaces/${workspaceId}/invites failed: ${res.status}`);
  }
  return res.json();
}

export async function listWorkspaceInvites(workspaceId) {
  const res = await apiFetch(`/workspaces/${workspaceId}/invites`);
  if (!res.ok) throw new Error(`GET /workspaces/${workspaceId}/invites failed: ${res.status}`);
  return res.json();
}

export async function revokeWorkspaceInvite(workspaceId, inviteId) {
  const res = await apiFetch(`/workspaces/${workspaceId}/invites/${inviteId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE /workspaces/${workspaceId}/invites/${inviteId} failed: ${res.status}`);
}

export async function resendWorkspaceInvite(workspaceId, inviteId) {
  const res = await apiFetch(`/workspaces/${workspaceId}/invites/${inviteId}/resend`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /workspaces/${workspaceId}/invites/${inviteId}/resend failed: ${res.status}`);
  return res.json();
}

export async function fetchWorkspaceInvitePreview(token) {
  const res = await apiFetch(`/workspace-invites/${token}`);
  if (!res.ok) throw new Error(`GET /workspace-invites/${token} failed: ${res.status}`);
  return res.json();
}

export async function acceptWorkspaceInvite(token) {
  const res = await apiFetch(`/workspace-invites/${token}/accept`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /workspace-invites/${token}/accept failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- teamspaces
export async function fetchWorkspaceTeamspaces(workspaceId) {
  const res = await apiFetch(`/workspaces/${workspaceId}/teamspaces`);
  if (!res.ok) throw new Error(`GET /workspaces/${workspaceId}/teamspaces failed: ${res.status}`);
  return res.json();
}

// 이 워크스페이스에서의 내 개인 페이지(나만 보는 팀스페이스) — 없으면 서버가 만든다.
export async function fetchPersonalSpace(workspaceId) {
  const res = await apiFetch(`/workspaces/${workspaceId}/personal-space`);
  if (!res.ok) throw new Error(`GET /workspaces/${workspaceId}/personal-space failed: ${res.status}`);
  return res.json(); // { id, name, visibility: "private", my_role: "admin", personal: true, project_count }
}

export async function createTeamspace(workspaceId, name, { description, visibility = "open" } = {}) {
  const res = await apiFetch(`/workspaces/${workspaceId}/teamspaces`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, description: description || null, visibility }),
  });
  if (!res.ok) throw new Error(`POST /workspaces/${workspaceId}/teamspaces failed: ${res.status}`);
  return res.json();
}

export async function fetchTeamspace(teamspaceId) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}`);
  if (!res.ok) throw new Error(`GET /teamspaces/${teamspaceId} failed: ${res.status}`);
  return res.json();
}

export async function updateTeamspace(teamspaceId, name, { description, visibility } = {}) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name, description: description || null, visibility }),
  });
  if (!res.ok) throw new Error(`PATCH /teamspaces/${teamspaceId} failed: ${res.status}`);
  return res.json();
}

export async function deleteTeamspace(teamspaceId) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}`, { method: "DELETE" });
  if (!res.ok) await throwWithDetail(res, `DELETE /teamspaces/${teamspaceId}`);
}

export async function joinTeamspace(teamspaceId) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}/join`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /teamspaces/${teamspaceId}/join failed: ${res.status}`);
  return res.json();
}

export async function updateTeamspaceMemberRole(teamspaceId, memberId, role) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}/members/${memberId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ role }),
  });
  if (!res.ok) await throwWithDetail(res, `PATCH /teamspaces/${teamspaceId}/members/${memberId}`);
  return res.json();
}

export async function updateTeamspaceMemberDiscipline(teamspaceId, memberId, discipline) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}/members/${memberId}/discipline`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ discipline: discipline || null }),
  });
  if (!res.ok) {
    throw new Error(`PATCH /teamspaces/${teamspaceId}/members/${memberId}/discipline failed: ${res.status}`);
  }
  return res.json();
}

export async function removeTeamspaceMember(teamspaceId, memberId) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}/members/${memberId}`, { method: "DELETE" });
  if (!res.ok) await throwWithDetail(res, `DELETE /teamspaces/${teamspaceId}/members/${memberId}`);
}

export async function leaveTeamspace(teamspaceId) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}/leave`, { method: "POST" });
  if (!res.ok) await throwWithDetail(res, `POST /teamspaces/${teamspaceId}/leave`);
}

export async function createTeamspaceInvite(teamspaceId, role, targetEmail, discipline) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}/invites`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ role, target_email: targetEmail || null, discipline: discipline || null }),
  });
  if (!res.ok) {
    throw new Error((await res.json().catch(() => ({}))).detail || `POST /teamspaces/${teamspaceId}/invites failed: ${res.status}`);
  }
  return res.json();
}

export async function listTeamspaceInvites(teamspaceId) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}/invites`);
  if (!res.ok) throw new Error(`GET /teamspaces/${teamspaceId}/invites failed: ${res.status}`);
  return res.json();
}

export async function revokeTeamspaceInvite(teamspaceId, inviteId) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}/invites/${inviteId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE /teamspaces/${teamspaceId}/invites/${inviteId} failed: ${res.status}`);
}

export async function resendTeamspaceInvite(teamspaceId, inviteId) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}/invites/${inviteId}/resend`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /teamspaces/${teamspaceId}/invites/${inviteId}/resend failed: ${res.status}`);
  return res.json();
}

export async function fetchTeamspaceInvitePreview(token) {
  const res = await apiFetch(`/teamspace-invites/${token}`);
  if (!res.ok) throw new Error(`GET /teamspace-invites/${token} failed: ${res.status}`);
  return res.json();
}

export async function acceptTeamspaceInvite(token) {
  const res = await apiFetch(`/teamspace-invites/${token}/accept`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /teamspace-invites/${token}/accept failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- projects
export async function fetchProjects(workspaceId) {
  const res = await apiFetch(`/workspaces/${workspaceId}/projects`);
  if (!res.ok) throw new Error(`GET /workspaces/${workspaceId}/projects failed: ${res.status}`);
  return res.json();
}

export async function fetchTeamspaceProjects(teamspaceId, q = "") {
  const url = new URL(`${API_BASE}/teamspaces/${teamspaceId}/projects`);
  if (q) url.searchParams.set("q", q);
  const res = await apiFetch(url.pathname + url.search);
  if (!res.ok) throw new Error(`GET /teamspaces/${teamspaceId}/projects failed: ${res.status}`);
  return res.json();
}

export async function createProject(teamspaceId, name, { description, leadMemberId, leadRole } = {}) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}/projects`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      name,
      description: description || null,
      lead_member_id: leadMemberId || null,
      lead_role: leadRole || "manager",
    }),
  });
  if (!res.ok) throw new Error(`POST /teamspaces/${teamspaceId}/projects failed: ${res.status}`);
  return res.json();
}

export async function generateProjectSpec(projectId, { repoLanguage, repoDescription, repoTopics } = {}) {
  const res = await apiFetch(`/projects/${projectId}/generate-spec`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      repo_language: repoLanguage || null,
      repo_description: repoDescription || null,
      repo_topics: repoTopics || [],
    }),
  });
  if (!res.ok) throw new Error(`POST /projects/${projectId}/generate-spec failed: ${res.status}`);
  return res.json();
}

export async function fetchProject(projectId) {
  const res = await apiFetch(`/projects/${projectId}`);
  if (!res.ok) throw new Error(`GET /projects/${projectId} failed: ${res.status}`);
  return res.json();
}

export async function updateProject(projectId, name) {
  const res = await apiFetch(`/projects/${projectId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!res.ok) throw new Error(`PATCH /projects/${projectId} failed: ${res.status}`);
  return res.json();
}

export async function deleteProject(projectId) {
  const res = await apiFetch(`/projects/${projectId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE /projects/${projectId} failed: ${res.status}`);
}

export async function moveProject(projectId, teamspaceId) {
  const res = await apiFetch(`/projects/${projectId}/move`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ teamspace_id: teamspaceId }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `POST /projects/${projectId}/move failed: ${res.status}`);
  return res.json();
}

export async function updateProjectMemberRole(projectId, memberId, role) {
  const res = await apiFetch(`/projects/${projectId}/members/${memberId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ role }),
  });
  if (!res.ok) throw new Error(`PATCH /projects/${projectId}/members/${memberId} failed: ${res.status}`);
  return res.json();
}

export async function removeProjectMember(projectId, memberId) {
  const res = await apiFetch(`/projects/${projectId}/members/${memberId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE /projects/${projectId}/members/${memberId} failed: ${res.status}`);
}

export async function leaveProject(projectId) {
  const res = await apiFetch(`/projects/${projectId}/leave`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /projects/${projectId}/leave failed: ${res.status}`);
}

export async function createProjectInvite(projectId, role, targetEmail) {
  const res = await apiFetch(`/projects/${projectId}/invites`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ role, target_email: targetEmail || null }),
  });
  if (!res.ok) {
    throw new Error((await res.json().catch(() => ({}))).detail || `POST /projects/${projectId}/invites failed: ${res.status}`);
  }
  return res.json();
}

export async function listProjectInvites(projectId) {
  const res = await apiFetch(`/projects/${projectId}/invites`);
  if (!res.ok) throw new Error(`GET /projects/${projectId}/invites failed: ${res.status}`);
  return res.json();
}

export async function revokeProjectInvite(projectId, inviteId) {
  const res = await apiFetch(`/projects/${projectId}/invites/${inviteId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE /projects/${projectId}/invites/${inviteId} failed: ${res.status}`);
}

export async function resendProjectInvite(projectId, inviteId) {
  const res = await apiFetch(`/projects/${projectId}/invites/${inviteId}/resend`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /projects/${projectId}/invites/${inviteId}/resend failed: ${res.status}`);
  return res.json();
}

export async function fetchProjectInvitePreview(token) {
  const res = await apiFetch(`/project-invites/${token}`);
  if (!res.ok) throw new Error(`GET /project-invites/${token} failed: ${res.status}`);
  return res.json();
}

export async function acceptProjectInvite(token) {
  const res = await apiFetch(`/project-invites/${token}/accept`, { method: "POST" });
  if (!res.ok) throw new Error(`POST /project-invites/${token}/accept failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- project <-> repository
export async function fetchProjectRepositories(projectId) {
  const res = await apiFetch(`/projects/${projectId}/repositories`);
  if (!res.ok) throw new Error(`GET /projects/${projectId}/repositories failed: ${res.status}`);
  return res.json();
}

export async function attachRepositoryToProject(projectId, repositoryId) {
  const res = await apiFetch(`/projects/${projectId}/repositories`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ repository_id: repositoryId }),
  });
  if (!res.ok) throw new Error(`POST /projects/${projectId}/repositories failed: ${res.status}`);
  return res.json();
}

// 분석 진행 상태(app/analysis.py) — 채팅 잠금 패널과 완료 알림이 쓴다.
// state: no_repository | analyzing | failed | ready | ready_reanalyzing
export async function fetchProjectAnalysis(projectId) {
  const res = await apiFetch(`/projects/${projectId}/analysis`);
  if (!res.ok) throw new Error(`GET /projects/${projectId}/analysis failed: ${res.status}`);
  return res.json();
}

export async function detachRepositoryFromProject(projectId, repositoryId) {
  const res = await apiFetch(`/projects/${projectId}/repositories/${repositoryId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE /projects/${projectId}/repositories/${repositoryId} failed: ${res.status}`);
}

export async function fetchRepositoryLanguages(repositoryId) {
  const res = await apiFetch(`/repositories/${repositoryId}/languages`);
  if (!res.ok) throw new Error(`GET /repositories/${repositoryId}/languages failed: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------- 접속 상태 (app/presence.py)
export async function sendPresence(teamspaceId, state) {
  const res = await apiFetch("/presence", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ teamspace_id: teamspaceId || null, state }),
  });
  if (!res.ok) throw new Error(`POST /presence failed: ${res.status}`);
}

// 탭을 닫을 때 — fetch 는 페이지가 사라지며 취소될 수 있어 sendBeacon 으로 보낸다(쿠키 포함, 같은 오리진).
export function sendPresenceOfflineBeacon() {
  const blob = new Blob([JSON.stringify({ state: "offline" })], { type: "application/json" });
  return navigator.sendBeacon?.(`${API_BASE}/api/presence`, blob);
}

export async function fetchTeamspacePresence(teamspaceId) {
  const res = await apiFetch(`/teamspaces/${teamspaceId}/presence`);
  if (!res.ok) throw new Error(`GET presence failed: ${res.status}`);
  return res.json(); // [{id, nickname, name, avatar_url, role, state}]
}
