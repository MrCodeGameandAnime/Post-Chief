import { useState } from "react";
import { request, send } from "../../../packages/api-client/src";

interface Boards {
  boards: { id: string; name: string }[];
  bookmark: string | null;
  selected_id: string | null;
  selected_name: string | null;
}
type Run = (action: () => Promise<unknown>) => Promise<boolean>;

export function PinterestBoards({ accountId, run, busy }: {
  accountId: string; run: Run; busy: boolean;
}) {
  const [data, setData] = useState<Boards | null>(null);
  const load = (bookmark?: string) => run(async () => {
    const next = await request<Boards>(`/connections/pinterest/${accountId}/boards` +
      (bookmark ? `?bookmark=${encodeURIComponent(bookmark)}` : ""));
    setData({ ...next, boards: bookmark ? [...(data?.boards ?? []), ...next.boards] : next.boards });
  });
  return <div>
    <p>Choose a public board before creating Pinterest drafts. Each delivery retains its chosen board when preparation starts.</p>
    <button disabled={busy} onClick={() => load()}>Choose Pinterest board</button>
    {data && <>
      <p>{data.selected_name ? `Selected: ${data.selected_name}` : "No board selected."}</p>
      {data.boards.map((board) => <button key={board.id} disabled={busy || data.selected_id === board.id}
        onClick={() => run(async () => {
          await send(`/connections/pinterest/${accountId}/board`, { board_id: board.id });
          setData({ ...data, selected_id: board.id, selected_name: board.name });
        })}>Use {board.name}</button>)}
      {!data.boards.length && <p>No public boards in this page. Create a public board in Pinterest if needed.</p>}
      {data.bookmark && <button disabled={busy} onClick={() => load(data.bookmark!)}>More boards</button>}
    </>}
  </div>;
}
