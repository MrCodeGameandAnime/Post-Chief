export type PublicationStatus =
  | "pending"
  | "processing"
  | "published"
  | "failed"
  | "retrying"
  | "cancelled";
export type ProviderName =
  | "facebook"
  | "instagram"
  | "threads"
  | "x"
  | "pinterest"
  | "bluesky"
  | "linkedin";
export interface Connection {
  id: string;
  provider: ProviderName;
  name: string;
  remote_id: string;
  active: boolean;
  expires_at: string | null;
}
export interface Publication {
  id: string;
  account_id: string;
  provider: ProviderName;
  account_name: string;
  status: PublicationStatus;
  url: string | null;
  attempts: number;
  published_at?: string | null;
  provider_id?: string | null;
  error: { message: string; action_required: string } | null;
}
export interface Campaign {
  id: string;
  title: string;
  body: string;
  asset_ids: string[];
  overrides: Partial<
    Record<ProviderName, { body?: string; asset_ids?: string[] }>
  >;
  scheduled_at: string | null;
  status: string;
  revision: number;
  publications: Publication[];
  external_posts?: ExternalPost[];
}
export interface ExternalPost {
  id: string;
  provider: "x";
  url: string;
  body: string;
  asset_ids: string[];
  published_at: string;
  delivery: "manual";
  verification: "owner_reported";
}
export interface Asset {
  id: string;
  name: string;
  mime_type: string;
  byte_size: number;
  source: string;
  file_url: string;
  details: Record<string, unknown>;
}
export interface Session {
  email: string;
  csrf: string;
  kind: string;
}
