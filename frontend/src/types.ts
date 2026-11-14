export type ObligationStatus =
  | "pending"
  | "scheduled"
  | "attempted"
  | "patient_reported"
  | "verified"
  | "overdue"
  | "cancelled"
  | "escalated"
  | "unknown"
  | "conflict";

export interface Conflict {
  field: string;
  recorded_value: string;
  reported_value: string;
  reported_by?: string;
  detected_at?: string;
  resolved?: boolean;
  resolved_at?: string;
  kept?: string;
  superseded?: string;
  resolved_by?: string;
}

export interface Obligation {
  id: string;
  patient_id: string;
  episode_id?: string;
  description: string;
  category?: string;
  owner: string;
  due_date?: string;
  status: ObligationStatus;
  raw_status?: string;
  status_derived?: boolean;
  is_open: boolean;
  days_until_due?: number | null;
  days_overdue?: number;
  priority_rank?: number;
  verification_required?: boolean;
  confidence?: number;
  latest_evidence?: string;
  confirmed_date?: string;
  confirmed_source?: string;
  next_action?: string;
  conflicts?: Conflict[];
  source?: { event_id?: string; label?: string; quote?: string };
  evidence_ids?: string[];
  history?: { at: string; status: string; actor: string; reason: string }[];
  origin?: "ehr_structured" | "ai_extracted" | string;
  ehr_kind?: string;
  fhir_refs?: string[];
  classified_by?: ClassifiedBy;
  /** Narrative's category for an unsettled EHR order (from a linked AI follow-up). */
  suggested_category?: string;
  /** AI-extracted follow-up that may be the same as this EHR order. */
  possible_duplicate_of?: string;
  duplicate_of?: string;
  version?: number;
  created_at?: string;
  updated_at?: string;
  consult_note_ref?: string | null;
  clinic_completed_on?: string | null;
  assignee?: { id: string; name: string } | null;
}

export interface Patient {
  id: string;
  name: string;
  initials?: string;
  age: number;
  mrn: string;
  condition: string;
  risk: string;
  pronouns?: string;
  phone?: string;
  email?: string;
  birth_date?: string | null;
  caregiver?: string;
  caregiver_contact?: CaregiverContact | null;
  preferences?: {
    channel?: string;
    best_time?: string;
    language?: string;
    interpreter_needed?: boolean;
    timezone?: string | null;
    consent_to_automated_calls?: boolean;
    consent_to_recording?: boolean;
    accessibility?: string;
  };
  synthetic?: boolean;
  identity_review?: { status: "needed" | "dismissed" | string; candidates?: string[]; reason?: string };
  ehr?: { fhir_base?: string; fhir_id?: string; identifiers?: { system?: string; value: string; type?: string }[] };
}

export interface CaregiverContact {
  name?: string | null;
  phone?: string | null;
  relationship?: string | null;
  may_discuss_care?: boolean;
}

export interface PatientUpdate {
  name?: string;
  birth_date?: string;
  mrn?: string;
  condition?: string;
  risk?: "Low" | "Medium" | "High";
  pronouns?: string;
  phone?: string;
  email?: string;
  caregiver?: CaregiverContact;
  preferences?: {
    channel?: "Voice call" | "Text" | "Email" | "Portal";
    best_time?: string;
    language?: string;
    interpreter_needed?: boolean;
    timezone?: string;
    consent_to_automated_calls?: boolean;
    consent_to_recording?: boolean;
  };
}

export interface DuplicateCandidate {
  id: string;
  name: string;
  birth_date?: string | null;
  phone?: string;
  mrn?: string;
  age?: number;
  condition?: string;
  match_reasons: string[];
}

export interface PatientSummary extends Patient {
  open_count: number;
  overdue_count: number;
  conflict_count: number;
  escalation_count: number;
  pending_verification: number;
  next_due?: string | null;
  priority: Obligation[];
}

export interface Evidence {
  id: string;
  patient_id: string;
  obligation_id: string;
  kind: string;
  summary: string;
  source_ref?: string;
  quote?: string;
  confidence: number;
  recorded_at: string;
  recorded_by: string;
}

export interface EventItem {
  id: string;
  patient_id: string;
  episode_id?: string;
  type: string;
  title: string;
  detail: string;
  actor?: string;
  occurred_at: string;
  source_ref?: string;
  transcript?: { role: string; message: string; time_in_call_secs?: number }[];
  metadata?: Record<string, unknown>;
}

export interface Memory {
  id: string;
  patient_id: string;
  kind: string;
  summary: string;
  content?: string;
  tags?: string[];
  importance?: number;
  retrieval?: string;
  score?: number;
  created_at: string;
  source_event_id?: string;
}

export interface Approval {
  id: string;
  patient_id: string;
  run_id?: string;
  thread_id?: string;
  kind: string;
  mode: "queued" | "interrupt";
  obligation_id?: string | null;
  title: string;
  rationale: string;
  proposed_changes: Record<string, unknown>;
  evidence_summary?: string;
  confidence?: number;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  resolved_at?: string | null;
  resolved_by?: string | null;
  resolution_note?: string | null;
  applied_at?: string | null;
  proposal?: Record<string, unknown>;
  source?: string;
  clinic_evidence?: { source_ref: string; evidence_id: string } | null;
  /** 0 emergency, 1 urgent, 2 everything else (pending queue order). */
  priority?: number;
  urgency?: "urgent" | "emergency";
  pages?: { at: string; attempt: number; ok: boolean; detail: string }[];
  acknowledged_at?: string | null;
  acknowledged_by?: string | null;
  chosen_date?: string | null;
}

export interface AuditEntry {
  id: string;
  patient_id: string;
  thread_id?: string | null;
  run_id?: string | null;
  actor: string;
  action: string;
  detail: string;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  evidence_id?: string | null;
  created_at: string;
}

export interface VoiceCall {
  id: string;
  patient_id: string;
  direction: "inbound" | "outbound";
  elevenlabs_conversation_id?: string;
  started_at: string;
  duration_secs?: number;
  outcome: string;
  transcript?: { role: string; message: string; time_in_call_secs?: number }[];
  agent_summary?: string;
  created_at: string;
}

export interface Checkin {
  id: string;
  patient_id: string;
  scheduled_for: string;
  reason: string;
  status: string;
}

export interface Episode {
  id: string;
  patient_id: string;
  admission_date: string;
  discharge_date: string;
  reason: string;
  status: string;
  discharge_summary?: string;
}

export interface Dashboard {
  patients: PatientSummary[];
  stats: {
    patients: number;
    open_obligations: number;
    overdue: number;
    conflicts: number;
    awaiting_verification: number;
    escalated: number;
    pending_approvals: number;
    verified_this_episode: number;
  };
  priority_obligations: Obligation[];
  pending_approvals: Approval[];
  recent_events: EventItem[];
  recent_audit: AuditEntry[];
}

export interface Journey {
  patient: Patient;
  episode?: Episode;
  obligations: Obligation[];
  evidence: Record<string, Evidence[]>;
  events: EventItem[];
  memories: Memory[];
  approvals: Approval[];
  audit: AuditEntry[];
  voice_calls: VoiceCall[];
  checkins: Checkin[];
  tcm?: TcmState;
}

export type ContactOutcome =
  | "reached"
  | "reached_caregiver"
  | "voicemail"
  | "no_answer"
  | "wrong_number"
  | "declined"
  | "message_sent";

export interface ContactAttempt {
  id: string;
  at: string;
  day?: string;
  channel: string;
  direction: string;
  outcome: ContactOutcome;
  interactive: boolean;
  note?: string | null;
  actor: string;
}

export interface TcmState {
  eligible: boolean;
  reason?: string | null;
  episode_id?: string;
  discharge_date?: string;
  contact_due?: string;
  contact_status?: "met" | "attempts_documented" | "missed" | "due_today" | "open";
  contact_attempts?: number;
  contact_required_attempts?: number;
  contact_made_at?: string | null;
  visit_window_days?: 7 | 14;
  visit_window_source?: "risk" | "clinician";
  visit_due?: string;
  visit_status?: "met" | "booked" | "booked_late" | "missed" | "not_booked";
  visit_obligation_id?: string | null;
  visit_date?: string | null;
  med_rec_due?: string;
  code?: "99495" | "99496";
  days_since_discharge?: number;
  contacts?: ContactAttempt[];
}

export interface TcmRow {
  patient_id: string;
  name: string;
  risk: string;
  discharge_date: string;
  contact_due: string;
  contact_attempts: number;
  visit_due: string;
  code: string;
}

export interface TcmWorklist {
  today: string;
  lanes: { contact_due: TcmRow[]; contact_missed: TcmRow[]; visit_unbooked: TcmRow[]; visit_missed: TcmRow[] };
}

export interface Proposal {
  id: string;
  kind: string;
  title: string;
  rationale: string;
  obligation_id?: string | null;
  confidence?: number;
  requires_approval?: boolean;
  interrupt?: boolean;
  approval_id?: string | null;
}

export interface AgentResult {
  status: "completed" | "awaiting_approval";
  run_id?: string;
  patient_id?: string;
  reply: string;
  spoken_response?: string;
  extraction?: Record<string, unknown>;
  proposals?: Proposal[];
  notes?: string[];
  applied?: string[];
  interrupted?: boolean;
  pending_approvals?: Approval[];
  retrieval?: { counts?: Record<string, number>; memories?: Memory[] };
}

export interface ThreadState {
  thread_id: string;
  paused: boolean;
  next: string[];
  values: Record<string, unknown>;
  pending_approvals: Approval[];
  checkpoint_id?: string;
}

export interface AppConfig {
  app_name: string;
  demo_mode: boolean;
  demo_today: string;
  voice_enabled: boolean;
  elevenlabs_agent_id: string | null;
  langsmith_url: string | null;
  llm_enabled: boolean;
  model: string;
  scenario: {
    day: number;
    title: string;
    channel: string;
    capability: string;
    detail: string;
  }[];
  disclaimer: string;
}

export interface IntakeObligation {
  description: string;
  owner?: string;
  due_date?: string;
}

export interface PatientIntake {
  name: string;
  age?: number;
  mrn?: string;
  condition?: string;
  risk: "Low" | "Medium" | "High";
  pronouns?: string;
  phone?: string;
  email?: string;
  birth_date?: string;
  caregiver?: string;
  caregiver_phone?: string;
  caregiver_relationship?: string;
  caregiver_may_discuss_care?: boolean;
  interpreter_needed?: boolean;
  timezone?: string;
  confirm_new?: boolean;
  admission_date?: string;
  discharge_date?: string;
  reason?: string;
  discharge_summary?: string;
  channel: "Voice call" | "Text" | "Email" | "Portal";
  best_time?: string;
  language?: string;
  consent_to_automated_calls: boolean;
  consent_to_recording: boolean;
  obligations: IntakeObligation[];
}

export interface VoiceContext {
  patient_id: string;
  patient_name: string;
  first_name: string;
  preferred_language: string;
  preferences: Record<string, unknown>;
  open_obligations: Obligation[];
  context: string;
  disclosure: string;
  escalation_script: string;
}

export interface ClassifiedBy {
  method: "known_code" | "similar_order" | "similar_order_lexical" | "unrecognized" | string;
  matched?: string;
  score?: number;
  source_ref?: string;
  category?: string;
  confirmed?: boolean;
  confirmed_by?: string;
  confirmed_at?: string;
  outcome?: "confirmed" | "corrected" | "learned";
  original_category?: string;
}

export type IngestStepName = "pull" | "identify" | "structured" | "narrative" | "verify";

export interface IngestStep {
  status: "pending" | "running" | "done" | "failed";
  attempts: number;
  summary?: string;
  error?: string;
  started_at?: string;
  finished_at?: string;
  worker?: string;
}

export interface IngestJob {
  id: string;
  key: string;
  status: "queued" | "running" | "done" | "failed";
  source: string;
  actor?: string;
  params: Record<string, unknown>;
  steps: Record<IngestStepName, IngestStep>;
  step_order?: IngestStepName[];
  attempts: number;
  current_step?: string | null;
  patient_id?: string;
  error?: string;
  lease_owner?: string | null;
  lease_until?: string | null;
  result?: { summary?: string; patient_id?: string; identity_review?: boolean } & Record<string, unknown>;
  created_at: string;
  updated_at?: string;
  finished_at?: string;
}

export type ActionStatus = "intended" | "sent" | "confirmed" | "failed" | "unconfirmed" | "unknown";

export interface LedgerAction {
  id: string;
  key?: string | null;
  kind: string;
  patient_id: string;
  obligation_id?: string | null;
  summary: string;
  proof_expected?: string;
  actor: string;
  status: ActionStatus;
  intended_at: string;
  confirm_by?: string;
  external_ref?: string | null;
  confirmation?: string;
  history: { at: string; status: ActionStatus; detail: string }[];
  metadata?: Record<string, unknown>;
}

export interface LedgerSummary {
  counts: Record<ActionStatus, number>;
  needs_attention: number;
}

export interface ReviewCategory {
  category: string;
  label: string;
  owner?: string | null;
  kind: string;
}

export interface IdentityView {
  id: string;
  name: string;
  mrn?: string;
  birth_date?: string | null;
  age?: number;
  phone?: string;
  condition?: string;
  ehr_fhir_id?: string | null;
  ehr_identifiers: { system?: string; value: string; type?: string }[];
  latest_discharge?: string | null;
  facility?: string | null;
  created_at?: string;
  episodes: number;
  follow_ups: number;
  open_follow_ups: number;
  memories: number;
}

export interface IdentityCase {
  patient: IdentityView;
  reason?: string;
  status: string;
  candidates: IdentityView[];
}

export interface PossibleDuplicate {
  id: string;
  description: string;
  category: string;
  quote?: string | null;
  due_date?: string | null;
}

export interface ReviewQueue {
  orders: (Obligation & {
    patient_name?: string;
    source_order?: { ref: string; code?: string | null; display: string; performer?: string | null; kind: string } | null;
    possible_duplicates?: PossibleDuplicate[];
  })[];
  identities: IdentityCase[];
  categories: ReviewCategory[];
  counts: { orders: number; identities: number; total: number };
}

// ── Call Room ────────────────────────────────────────────────────────────
export type CallMode = "browser" | "phone" | "simulated";

export interface CallModes {
  browser: { available: boolean; why: string | null };
  phone: { available: boolean; why: string | null };
  simulated: { available: boolean; why: string | null };
  agent_id?: string | null;
}

export interface CallAgendaItem {
  key: string;
  kind: "step" | "followup";
  title: string;
  why: string;
  status?: string;
  category?: string;
}

export interface CallBrief {
  patient_id: string;
  patient_name?: string;
  language?: string;
  preferred_channel?: string;
  best_time?: string;
  consent_to_automated_calls?: boolean;
  consent_to_recording?: boolean;
  phone?: string;
  agenda: CallAgendaItem[];
  remembered: string[];
}

export interface CallEffect {
  type: "status_changed" | "followup_created" | "conflict_recorded" | "date_reported" | "approval_queued";
  title?: string;
  obligation_id?: string;
  approval_id?: string;
  from?: string;
  to?: string;
  verified?: boolean;
  date?: string;
  kind?: string;
  audience?: string;
}

export interface CallEvent {
  id: string;
  at: string;
  kind: "transcript" | "tool" | "system";
  role?: "agent" | "patient";
  text: string;
  tool?: string;
  effects?: CallEffect[];
  detail?: Record<string, unknown>;
}

export interface CallSession {
  id: string;
  patient_id: string;
  mode: CallMode;
  status: "preparing" | "live" | "ended" | "failed";
  brief: CallBrief;
  events: CallEvent[];
  effects: CallEffect[];
  action_id: string;
  conversation_id?: string | null;
  phone?: string | null;
  created_at: string;
  started_at?: string;
  ended_at?: string;
  error?: string;
}

export interface StaffUser {
  id: string;
  name: string;
  role: "coordinator" | "clinician" | string;
}

export interface MyWork {
  today: string;
  role: string;
  assigned: (Obligation & { patient_name?: string })[];
  unassigned: (Obligation & { patient_name?: string })[];
  approvals_pending: number;
  tcm: TcmWorklist["lanes"];
}

export interface PortalFollowUp {
  id: string;
  description: string;
  due_date?: string | null;
  confirmed_date?: string | null;
  status?: string;
}
