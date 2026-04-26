export interface TranscriptSummary {
  id: string;
  title: string;
  duration_sec: number | null;
  language: string | null;
  diarized: boolean;
  speaker_count?: number;
  model: string | null;
  segment_count: number;
  tags?: string[];
  channel?: string | null;
  channel_url?: string | null;
  upload_date?: string | null;     // "YYYYMMDD"
  view_count?: number | null;
  like_count?: number | null;
  /** Projects this video belongs to. Empty array when unaffiliated. Used by
   *  the library for badges + filtering. */
  project_ids?: string[];
}

export interface VideoMeta {
  tags: string[];
  speaker_names: Record<string, string>;
  notes: string;
  updated_at: string | null;
}

export interface Segment {
  id: number;
  start: number;
  end: number;
  text: string;
  speaker?: string;
}

export interface Transcript {
  id: string;
  url: string;
  title: string;
  duration_sec: number | null;
  language: string | null;
  language_probability: number | null;
  model: string | null;
  compute_type: string | null;
  diarized: boolean;
  speaker_count?: number;
  // YouTube-side metadata (captured at ingest time; re-fetchable)
  channel?: string | null;
  channel_id?: string | null;
  channel_url?: string | null;
  channel_follower_count?: number | null;
  upload_date?: string | null;   // "YYYYMMDD"
  view_count?: number | null;
  like_count?: number | null;
  comment_count?: number | null;
  description?: string | null;
  categories?: string[];
  yt_tags?: string[];
  transcription_elapsed_sec?: number | null;
  transcription_realtime_factor?: number | null;
  batched?: boolean | null;
  batch_size?: number | null;
  segments: Segment[];
}

export type SseEvent =
  | { event: "phase"; data: { phase: string; message: string } }
  | { event: "downloaded"; data: { id: string; title: string; duration_sec: number } }
  | { event: "language"; data: { language: string; probability: number | null } }
  | { event: "segment"; data: Segment }
  | {
      event: "done";
      data: {
        id: string;
        elapsed_sec: number;
        realtime_factor: number;
        duration_sec: number;
        files: Record<string, string>;
      };
    }
  | { event: "error"; data: { message: string } };

export interface Chapter {
  start: number;
  title: string;
  /** One-sentence description of what happens in this section. Added in the
   *  richer-insights schema; absent on legacy analyses. */
  note?: string;
}

export interface Highlight {
  start: number;
  end: number;
  speaker: string | null;
  quote: string;
  /** Under the richer prompt, this is phrased as "easy to miss because ___".
   *  Legacy analyses populated it with a generic "why this stands out" line;
   *  both render the same way in the UI. */
  reason: string;
}

export type CheckScreenSignal =
  | "deictic_reference"
  | "code_on_screen"
  | "diagram_drawn"
  | "matrix_math_shown"
  | "figure_reference"
  | "animation_or_transition";

export interface CheckScreenCandidate {
  t_sec: number;
  segment_id: number;
  trigger_text: string;
  signal: CheckScreenSignal;
  what_i_expect_to_see: string;
  priority: "high" | "medium" | "low";
}

/** Kinds of check-screen marks stored in the DB. `codex_suggested` comes from
 *  an AI analysis; `user_marked` is a user-created mark; `user_confirmed` is
 *  a codex suggestion the user accepted; `dismissed` tombstones a rejected
 *  suggestion so it doesn't re-appear. */
export type CheckScreenMarkKind =
  | "codex_suggested"
  | "user_marked"
  | "user_confirmed"
  | "dismissed";

/** A persisted annotation on a transcript moment. Row shape from
 *  `check_screen_marks`. Optional fields mirror NULL-able columns. */
export interface CheckScreenMark {
  id: number;
  video_id: string;
  t_sec: number;
  segment_id: number | null;
  kind: CheckScreenMarkKind;
  trigger_text: string | null;
  signal: CheckScreenSignal | string | null;
  what_i_expect_to_see: string | null;
  priority: "high" | "medium" | "low" | null;
  note: string | null;
  analysis_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface AnalysisMeta {
  generated_at: string | null;
  duration_ms: number | null;
  duration_api_ms?: number | null;
  num_turns?: number | null;
  cost_usd: number;
  tokens_in: number;
  tokens_out: number;
  cache_read_tokens?: number;
  cache_creation_tokens?: number;
}

export interface Analysis {
  /** Legacy short-summary field. New analyses may omit this and populate
   *  `value_prop` + `narrative_summary` instead. */
  summary?: string;
  /** 1-3 sentence payoff: what a viewer walks away with. */
  value_prop?: string;
  /** Multi-paragraph faithful retelling. */
  narrative_summary?: string;
  takeaways: string[];
  chapters: Chapter[];
  highlights: Highlight[];
  check_screen_candidates?: CheckScreenCandidate[];
  _meta?: AnalysisMeta;
}

export interface Project {
  id: string;
  name: string;
  description: string | null;
  video_count: number;
  total_seconds: number;
  last_activity: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectVideoEntry {
  id: string;
  title: string | null;
  duration_sec: number | null;
  channel: string | null;
  archived: boolean;
  added_at: string;
}

export interface ProjectDetail {
  project: Project;
  videos: ProjectVideoEntry[];
}

export interface StatsVideo {
  id: string;
  title: string | null;
  duration_sec: number | null;
  channel: string | null;
  created_at: string;
}

export interface ChannelStat {
  channel: string;
  count: number;
  total_seconds: number;
}

export interface LanguageStat {
  language: string;
  count: number;
}

export interface TagStat {
  tag: string;
  count: number;
}

export interface LongestVideoStat {
  id: string;
  title: string | null;
  duration_sec: number;
  channel: string | null;
}

export interface RecentAnalysisStat {
  id: string;
  title: string | null;
  finished_at: string | null;
  provider: string;
}

export interface Stats {
  video_count: number;
  project_count: number;
  total_seconds: number;
  storage_bytes: number;
  latest_videos: StatsVideo[];
  by_channel: ChannelStat[];
  by_language: LanguageStat[];
  top_tags: TagStat[];
  longest_videos: LongestVideoStat[];
  recently_analyzed: RecentAnalysisStat[];
}

// -------------------------------------------------------------------
// Slice 2 — Playlist preview + bulk ingest
// -------------------------------------------------------------------

export interface PlaylistEntry {
  id: string;
  title: string | null;
  duration_sec: number | null;
  thumbnail_url: string | null;
  url: string;
}

export interface PlaylistFailure {
  id: string | null;
  reason: string;
}

export interface PlaylistPreview {
  playlist_id: string;
  title: string | null;
  uploader: string | null;
  entry_count: number;
  entries: PlaylistEntry[];
  failures: PlaylistFailure[];
}

export interface BulkIngestSkipped {
  video_id: string;
  reason: "already_transcribed" | "archived" | string;
}

export interface BulkIngestResponse {
  job_ids: string[];
  skipped: BulkIngestSkipped[];
  project_id: string | null;
}

export interface BulkIngestRequest {
  project_id?: string | null;
  urls?: string[];
  playlist_url?: string;
  options?: { diarize?: boolean; model?: string; batched?: boolean };
  force?: boolean;
}

// -------------------------------------------------------------------
// Slice 3 — AI provider / model picker + live analysis telemetry
// -------------------------------------------------------------------

export interface AiProvider {
  /** Registry key, e.g. "claude_cli" / "ollama". */
  name: string;
  /** Human label, e.g. "Claude Code CLI". */
  display_name: string;
  /** False if the CLI is missing, the daemon is offline, etc. */
  available: boolean;
  /** One-line reason surfaced when `available === false`. */
  reason: string | null;
  /** Model ids for this provider — shown in the model dropdown. */
  models: string[];
}

export interface AnalysisStage {
  /** Human stage label (e.g. "Running tool: Read"). */
  stage: string;
  /** Client-side receive timestamp (Date.now() on arrival). */
  ts: number;
}

export interface AnalysisUsage {
  tokens_in?: number;
  tokens_out?: number;
  cached_tokens?: number;
  cost_usd?: number;
}
