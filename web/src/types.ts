export interface TranscriptSummary {
  id: string;
  title: string;
  duration_sec: number | null;
  language: string | null;
  diarized: boolean;
  model: string | null;
  segment_count: number;
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
