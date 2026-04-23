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
}

export interface Highlight {
  start: number;
  end: number;
  speaker: string | null;
  quote: string;
  reason: string;
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
  summary: string;
  takeaways: string[];
  chapters: Chapter[];
  highlights: Highlight[];
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

export interface Stats {
  video_count: number;
  project_count: number;
  total_seconds: number;
  storage_bytes: number;
  latest_videos: StatsVideo[];
}
