import { useEffect, useRef, useState } from "react";
import { request, send } from "../../../packages/api-client/src";
import { FeedbackReview, type FeedbackPreview } from "./FeedbackReview";
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
  const [preview, setPreview] = useState<FeedbackPreview | null>(null);
  const [result, setResult] = useState("");
  const trigger = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  useEffect(() => {
    if (!preview && !busy && restoreFocus.current) {
      trigger.current?.focus();
      restoreFocus.current = false;
    }
  }, [preview, busy]);
  const close = () => {
    restoreFocus.current = true;
    setPreview(null);
  };
  const refresh = () =>
    run(async () => {
      setPreview(await request(`/feedback/campaigns/${campaignId}/preview`));
      setResult("");
    });
  return (
    <div className="publication">
      <h3>GitHub feedback</h3>
      <p className="muted">
        Review the post record, publication ledger and native analytics before
        writing the selected workspace.
      </p>
      <button ref={trigger} disabled={busy} onClick={refresh}>
        Preview GitHub feedback
      </button>
      {preview && (
        <FeedbackReview
          preview={preview}
          busy={busy}
          onClose={close}
          onRefresh={refresh}
          onWrite={() =>
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
              close();
            })
          }
        />
      )}
      {result && <p role="status">{result}</p>}
    </div>
  );
}
