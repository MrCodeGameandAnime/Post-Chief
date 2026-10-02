import { useState } from "react";
import { request, send } from "../../../packages/api-client/src";

interface Preview {
  revision: number;
  digest: string;
  base_commit: string;
  workspace: string;
  branch: string;
  unchanged: boolean;
  files: { path: string; content: string }[];
}
type Run = (action: () => Promise<unknown>) => Promise<boolean>;
export function Feedback({
  campaignId,
  run,
  busy,
}: {
  campaignId: string;
  run: Run;
  busy: boolean;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [result, setResult] = useState("");
  return (
    <div className="publication">
      <h3>GitHub feedback</h3>
      <p className="muted">
        Review the post record, publication ledger and native analytics before
        writing the selected workspace.
      </p>
      <button
        disabled={busy}
        onClick={() =>
          run(async () => {
            setPreview(
              await request(`/feedback/campaigns/${campaignId}/preview`),
            );
            setResult("");
          })
        }
      >
        Preview GitHub feedback
      </button>
      {preview && (
        <>
          <p>
            {preview.workspace} · {preview.branch}
          </p>
          {preview.files.map((file) => (
            <details key={file.path}>
              <summary>{file.path}</summary>
              <pre>{file.content}</pre>
            </details>
          ))}
          {preview.unchanged ? (
            <p>Workspace already contains this feedback.</p>
          ) : (
            <button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const response = await send<{ sha: string }>(
                    `/feedback/campaigns/${campaignId}/sync`,
                    {
                      revision: preview.revision,
                      digest: preview.digest,
                      base_commit: preview.base_commit,
                    },
                  );
                  setResult("Written in commit " + response.sha);
                  setPreview(null);
                })
              }
            >
              Write reviewed feedback
            </button>
          )}
        </>
      )}
      {result && <p role="status">{result}</p>}
    </div>
  );
}
