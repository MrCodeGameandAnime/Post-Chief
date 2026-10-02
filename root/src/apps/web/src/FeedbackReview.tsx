import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface FeedbackPreview {
  revision: number;
  digest: string;
  base_commit: string;
  workspace: string;
  branch: string;
  unchanged: boolean;
  files: {
    path: string;
    content: string;
    diff: string;
    change: "created" | "updated" | "unchanged";
    additions: number;
    deletions: number;
  }[];
}

export function FeedbackReview({
  preview,
  busy,
  onClose,
  onWrite,
  onRefresh,
}: {
  preview: FeedbackPreview;
  busy: boolean;
  onClose: () => void;
  onWrite: () => Promise<boolean>;
  onRefresh: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const id = useId();
  const [selected, setSelected] = useState(0);
  const [full, setFull] = useState(false);
  const [error, setError] = useState("");
  const file = preview.files[selected];
  useEffect(() => setError(""), [preview]);
  useEffect(() => {
    const element = dialog.current!;
    const previous = document.body.style.overflow;
    element.showModal();
    document.body.style.overflow = "hidden";
    return () => {
      element.close();
      document.body.style.overflow = previous;
    };
  }, []);
  const changed = preview.files.filter((item) => item.change !== "unchanged");
  const created = changed.filter((item) => item.change === "created").length;
  const added = changed.reduce((total, item) => total + item.additions, 0);
  const removed = changed.reduce((total, item) => total + item.deletions, 0);
  return createPortal(
    <dialog
      ref={dialog}
      className="feedback-review"
      aria-labelledby={id + "-title"}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <div className="feedback-review-header">
        <div>
          <h2 id={id + "-title"}>Review GitHub feedback</h2>
          <p className="muted">
            {preview.workspace} · {preview.branch}
          </p>
        </div>
        <button
          disabled={busy}
          onClick={onClose}
          aria-label="Close feedback review"
          autoFocus
        >
          Close
        </button>
      </div>
      <p className="feedback-change-summary">
        {changed.length} files · {created} created · {changed.length - created}{" "}
        updated · {added} lines added · {removed} removed
      </p>
      <div
        role="tablist"
        aria-label="Feedback files"
        className="feedback-file-tabs"
      >
        {preview.files.map((item, index) => (
          <button
            key={item.path}
            ref={(node) => {
              tabs.current[index] = node;
            }}
            role="tab"
            id={id + "-tab-" + index}
            aria-controls={id + "-panel"}
            aria-selected={selected === index}
            tabIndex={selected === index ? 0 : -1}
            onClick={() => {
              setSelected(index);
              setFull(false);
            }}
            onKeyDown={(event) => {
              let next = index;
              if (event.key === "ArrowRight")
                next = (index + 1) % preview.files.length;
              else if (event.key === "ArrowLeft")
                next =
                  (index + preview.files.length - 1) % preview.files.length;
              else if (event.key === "Home") next = 0;
              else if (event.key === "End") next = preview.files.length - 1;
              else return;
              event.preventDefault();
              setSelected(next);
              setFull(false);
              tabs.current[next]?.focus();
            }}
          >
            {item.path}
          </button>
        ))}
      </div>
      {file && (
        <section
          role="tabpanel"
          id={id + "-panel"}
          aria-labelledby={id + "-tab-" + selected}
          tabIndex={0}
          className="feedback-file-panel"
        >
          <div className="feedback-file-toolbar">
            <span>
              {file.change} · +{file.additions} / −{file.deletions} lines
            </span>
            <div>
              <button aria-pressed={!full} onClick={() => setFull(false)}>
                Changes
              </button>
              <button aria-pressed={full} onClick={() => setFull(true)}>
                Full file
              </button>
            </div>
          </div>
          {full ? (
            <pre className="feedback-code">{file.content}</pre>
          ) : file.diff ? (
            <pre
              className="feedback-code feedback-diff"
              aria-label="File changes"
            >
              {file.diff.split("\n").map((line, index) => (
                <span
                  key={index}
                  className={
                    line.startsWith("+++") ||
                    line.startsWith("---") ||
                    line.startsWith("@@")
                      ? "diff-heading"
                      : line.startsWith("+")
                        ? "diff-added"
                        : line.startsWith("-")
                          ? "diff-removed"
                          : ""
                  }
                >
                  {line || " "}
                </span>
              ))}
            </pre>
          ) : (
            <p className="feedback-no-changes">No changes in this file.</p>
          )}
        </section>
      )}
      <div className="feedback-review-footer">
        {error && (
          <p role="alert" className="notice error">
            {error}
          </p>
        )}
        <p className="muted">
          {preview.unchanged
            ? "Workspace already contains this feedback."
            : "Writes all three reviewed files together. Existing text outside this campaign’s section is preserved."}
        </p>
        <div className="toolbar">
          <button disabled={busy} onClick={onRefresh}>
            Refresh preview
          </button>
          {!preview.unchanged && (
            <button
              className="primary"
              disabled={busy}
              onClick={async () => {
                setError("");
                if (!(await onWrite()))
                  setError(
                    "Feedback was not written. Refresh the preview and review current changes before trying again.",
                  );
              }}
            >
              Write reviewed feedback
            </button>
          )}
        </div>
      </div>
    </dialog>,
    document.body,
  );
}
