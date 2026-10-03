import type { YouTubeOptions as Options } from "../../../packages/shared-types/src";

export function YouTubeOptions({ value, change, title }: { value?: Options; change: (value: Options) => void; title: string }) {
  const options = value ?? { privacy_status: "private" as const };
  return <fieldset>
    <legend>YouTube video settings</legend>
    <label>Video title
      <input maxLength={100} placeholder={title} value={options.title ?? ""}
        onChange={e => change({ ...options, title: e.target.value || undefined })} />
    </label>
    <label>Visibility
      <select value={options.privacy_status} onChange={e => change({ ...options, privacy_status: e.target.value as Options["privacy_status"] })}>
        <option value="private">Private</option><option value="unlisted">Unlisted</option><option value="public">Public</option>
      </select>
    </label>
    <label>Is this video made for kids?
      <select value={options.made_for_kids === undefined ? "" : String(options.made_for_kids)}
        onChange={e => change({ ...options, made_for_kids: e.target.value === "" ? undefined : e.target.value === "true" })}>
        <option value="">Choose the audience</option><option value="false">No</option><option value="true">Yes</option>
      </select>
    </label>
    <small>Choose one MP4 video. YouTube may restrict uploads to private while the app awaits approval.</small>
  </fieldset>;
}
