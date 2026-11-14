import type {
  AgentResult,
  AppConfig,
  Approval,
  AuditEntry,
  Dashboard,
  IngestJob,
  Journey,
  LedgerAction,
  LedgerSummary,
  ReviewQueue,
  Memory,
  Obligation,
  Patient,
  PatientIntake,
  PatientUpdate,
  ThreadState,
  TcmState,
  PortalFollowUp,
  StaffUser,
  MyWork,
  TcmWorklist,
  ContactAttempt,
  ContactOutcome,
  VoiceContext,
  CallBrief,
  CallMode,
  CallModes,
  CallSession,
} from "./types";

const configuredBase = (import.meta.env.VITE_API_URL as string | undefined)?.replace(/\/$/, "");
// Requests stay same-origin in both modes: Vite proxies /api to FastAPI during
// development, and FastAPI serves the production build itself. Set VITE_API_URL
// only when the API lives on a different host.
export const API_BASE = configuredBase || "";
const API_PREFIX = "/api";

// The signed-in coordinator's JWT. Kept at module scope so every request
// automatically carries the Authorization header once auth is established.
let authToken: string | null = null;
export function setAuthToken(token: string | null) {
  authToken = token;
}
export function getAuthToken(): string | null {
  return authToken;
}

class ApiError extends Error {
  status: number;
  /** The structured ``detail`` when the server sent an object (e.g. a 409 with candidates). */
  data?: unknown;
  constructor(status: number, detail: string, data?: unknown) {
    super(detail);
    this.status = status;
    this.data = data;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json", ...(init?.headers as Record<string, string> | undefined) };
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;
  const response = await fetch(`${API_BASE}${API_PREFIX}${path}`, {
    headers,
    ...init,
  });
  if (!response.ok) {
    let detail = response.statusText;
    let data: unknown;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (body.detail && typeof body.detail.message === "string") {
        detail = body.detail.message;
        data = body.detail;
      } else detail = JSON.stringify(body.detail || body);
    } catch {
      /* noop */
    }
    throw new ApiError(response.status, detail, data);
  }
  return response.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  config: () => request<AppConfig>("/config"),
  health: () => request<Record<string, unknown>>("/health"),
  auth: {
    demo: () =>
      request<{
        coordinator: { email: string; password: string; name: string; role: string };
        clinician: { email: string; password: string; name: string; role: string };
      }>("/auth/demo"),
    login: (email: string, password: string) =>
      post<{ token: string; user: { id: string; email: string; name: string; role: string } }>("/auth/login", {
        email,
        password,
      }),
    me: () => request<{ user: { id: string; email: string; name: string; role: string } }>("/auth/me"),
    logout: () => post<{ ok: boolean }>("/auth/logout"),
  },
  dashboard: () => request<Dashboard>("/dashboard"),
  journey: (patientId: string) => request<Journey>(`/patients/${patientId}/journey`),
  patients: () => request<{ patients: Dashboard["patients"] }>("/patients"),
  memories: (patientId: string, q?: string) =>
    request<{ memories: Memory[]; query?: string }>(
      `/patients/${patientId}/memories${q ? `?q=${encodeURIComponent(q)}` : ""}`,
    ),
  approvals: (params?: { patient_id?: string; status?: string }) => {
    const search = new URLSearchParams();
    if (params?.patient_id) search.set("patient_id", params.patient_id);
    if (params?.status !== undefined) search.set("status", params.status);
    const qs = search.toString();
    return request<{ approvals: Approval[]; count: number }>(`/agent/approvals${qs ? `?${qs}` : ""}`);
  },
  resolveApproval: (id: string, approve: boolean, note?: string, chosenDate?: string) =>
    post<{ status: string; patient_id: string; reply?: string; applied?: string[] }>(
      `/agent/approvals/${id}/resolve`,
      { approve, note, chosen_date: chosenDate },
    ),
  chat: (patientId: string, message: string) =>
    post<AgentResult>("/agent/chat", { patient_id: patientId, message }),
  thread: (patientId: string) => request<ThreadState>(`/agent/threads/${patientId}`),
  audit: (patientId?: string, limit = 200) => {
    const search = new URLSearchParams({ limit: String(limit) });
    if (patientId) search.set("patient_id", patientId);
    return request<{ audit: AuditEntry[] }>(`/agent/audit?${search.toString()}`);
  },
  changeStatus: (
    obligationId: string,
    body: {
      status: string;
      evidence_summary?: string;
      override_reason?: string;
      source_ref?: string;
      consult_note_ref?: string;
    },
  ) => post<{ obligation: Obligation }>(`/obligations/${obligationId}/status`, body),
  confirmDate: (obligationId: string, confirmedDate: string, sourceRef?: string) =>
    post<{ obligation: Obligation }>(
      `/obligations/${obligationId}/confirm?confirmed_date=${encodeURIComponent(confirmedDate)}${
        sourceRef ? `&source_ref=${encodeURIComponent(sourceRef)}` : ""
      }`,
    ),
  addEvidence: (obligationId: string, summary: string, sourceRef?: string) =>
    post<{ evidence: unknown }>(`/obligations/${obligationId}/evidence`, {
      summary,
      source_ref: sourceRef,
      kind: "clinic_confirmation",
    }),
  acknowledgeApproval: (id: string) => post<{ approval: Approval }>(`/agent/approvals/${id}/acknowledge`),
  bulkResolve: (ids: string[], approve: boolean, note?: string) =>
    post<{ resolved: number; results: { id: string; ok: boolean; status: string }[] }>("/agent/approvals/bulk-resolve", { ids, approve, note }),
  readmit: (
    patientId: string,
    body: { admission_date?: string; discharge_date: string; reason?: string; disposition?: string; discharge_summary?: string },
  ) => post<{ episode: { id: string }; carried_over: string[]; previous_episode_id: string | null }>(`/patients/${patientId}/episodes`, body),
  closeEpisode: (patientId: string, body: { reason: string; note?: string; close_open?: boolean }) =>
    post<{ episode_id: string; cancelled: string[] }>(`/patients/${patientId}/episodes/close`, body),
  users: () => request<{ users: StaffUser[] }>("/users"),
  assign: (obligationId: string, userId: string | null) =>
    post<{ obligation: Obligation }>(`/obligations/${obligationId}/assign`, { user_id: userId }),
  myWork: () => request<MyWork>("/work/me"),
  portalOpen: (token: string) => request<{ first_name: string; verify_with: "birth_date" | "phone_last4" }>(`/portal/checkin/${encodeURIComponent(token)}`),
  portalVerify: (token: string, answer: string) =>
    post<{ session: string; first_name: string; follow_ups: PortalFollowUp[] }>("/portal/checkin/verify", { token, answer }),
  portalSubmit: (body: { session: string; updates: { obligation_id: string; status: string; date?: string }[]; message?: string; callback?: boolean }) =>
    post<{ ok: boolean; message: string; escalated: boolean }>("/portal/checkin/submit", body),
  logContact: (patientId: string, body: { outcome: ContactOutcome; channel?: string; note?: string }) =>
    post<{ contact: ContactAttempt; tcm: TcmState }>(`/patients/${patientId}/contacts`, body),
  setVisitWindow: (patientId: string, days: 7 | 14 | null) =>
    request<{ tcm: TcmState }>(`/patients/${patientId}/tcm/visit-window`, { method: "PUT", body: JSON.stringify({ days }) }),
  tcmWorklist: () => request<TcmWorklist>("/tcm/worklist"),
  updatePatient: (patientId: string, body: PatientUpdate) =>
    request<{ patient: Patient }>(`/patients/${patientId}`, { method: "PATCH", body: JSON.stringify(body) }),
  createObligation: (body: { patient_id: string; description: string; owner?: string; due_date?: string }) =>
    post<{ obligation: Obligation }>("/obligations", body),
  addDocument: (patientId: string, body: { title: string; text: string; source_ref?: string }) =>
    post<AgentResult>(`/patients/${patientId}/documents`, body),
  createPatient: (body: PatientIntake) =>
    post<{ patient: { id: string; name: string }; obligations: Obligation[]; ingestion?: unknown }>(
      "/patients",
      body,
    ),
  voiceContext: (patientId: string) => request<VoiceContext>(`/voice/context/${patientId}`),
  demoDay: (day: number) => post<AgentResult & { narration: string; ok: boolean }>(`/demo/day/${day}`, { day }),
  sweep: (daysAhead = 0) =>
    post<{ ok: boolean; today: string; telephony: boolean; actions: { type: string }[]; counts: Record<string, number> }>(
      `/demo/sweep?days_ahead=${daysAhead}`,
    ),
  fhir: (resource: Record<string, unknown>) =>
    post<{ ok: boolean; applied: number; results: { outcome: string; detail: string }[] }>(`/integrations/fhir`, resource),
  calls: {
    modes: () => request<CallModes>("/calls/modes"),
    brief: (patientId: string) => request<CallBrief>(`/calls/brief/${patientId}`),
    forPatient: (patientId: string) =>
      request<{ active: CallSession | null; recent: CallSession[] }>(`/calls/patient/${patientId}`),
    get: (id: string) => request<{ session: CallSession }>(`/calls/${id}`),
    start: (patientId: string, mode: CallMode, phone?: string) =>
      post<{ session: CallSession }>("/calls", { patient_id: patientId, mode, phone }),
    connected: (id: string, conversationId?: string | null) =>
      post<{ session: CallSession }>(`/calls/${id}/connected`, { conversation_id: conversationId ?? null }),
    line: (id: string, role: "agent" | "patient", text: string) => post(`/calls/${id}/transcript`, { role, text }),
    end: (id: string) => post<{ session: CallSession }>(`/calls/${id}/end`),
  },
  ingestJobs: () => request<{ jobs: IngestJob[] }>("/ingest/jobs"),
  resumeJob: (jobId: string, force = false) =>
    post<{ job_id: string; status: string; resumed_from?: string | null; failed_step?: string; error?: string; detail?: string }>(
      `/ingest/jobs/${jobId}/resume${force ? "?force=true" : ""}`,
    ),
  actions: (params?: { patient_id?: string; status?: string; limit?: number }) => {
    const search = new URLSearchParams();
    if (params?.patient_id) search.set("patient_id", params.patient_id);
    if (params?.status) search.set("status", params.status);
    if (params?.limit) search.set("limit", String(params.limit));
    const qs = search.toString();
    return request<{ summary: LedgerSummary; actions: LedgerAction[] }>(`/actions${qs ? `?${qs}` : ""}`);
  },
  review: (patientId?: string) =>
    request<ReviewQueue>(`/review${patientId ? `?patient_id=${encodeURIComponent(patientId)}` : ""}`),
  reviewOrder: (obligationId: string, category: string, note?: string) =>
    post<{ obligation: Obligation; outcome: "confirmed" | "corrected"; duplicates: { id: string; resolution: "cancelled_duplicate" | "kept" }[] }>(`/review/orders/${obligationId}`, { category, note }),
  mergeIdentity: (patientId: string, into: string, note?: string) =>
    post<{ ok: boolean; merged_into: string; moved: Record<string, number> }>(`/review/identity/${patientId}/merge`, { into, note }),
  dismissIdentity: (patientId: string, note?: string) =>
    post<{ ok: boolean }>(`/review/identity/${patientId}/dismiss`, { note }),
  seed: (reset = false) => post<Record<string, unknown>>(`/demo/seed?reset=${reset}`),
};

export { ApiError };
