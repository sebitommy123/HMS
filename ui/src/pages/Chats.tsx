import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";

import { createConversation, listConversations } from "@/api/conversations";
import { relativeTime } from "@/lib/format";

export function Chats() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
  });

  const newChat = useMutation({
    mutationFn: () => createConversation({}),
    onSuccess: async (conv) => {
      await qc.invalidateQueries({ queryKey: ["conversations"] });
      navigate(`/chats/${conv.id}`);
    },
  });

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Chats</h1>
          <p className="text-sm text-zinc-600">
            Talk to Claude about your DataPro setup. The AI can list catalogs and run
            queries against your Trino federation.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            className="rounded border border-zinc-200 bg-white px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {isFetching ? "Refreshing…" : "Refresh"}
          </button>
          <button
            type="button"
            onClick={() => newChat.mutate()}
            disabled={newChat.isPending}
            className="rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="new-chat-button"
          >
            {newChat.isPending ? "Creating…" : "New chat"}
          </button>
        </div>
      </header>

      <div className="rounded-lg border border-zinc-200 bg-white" data-testid="chats-panel">
        {isLoading ? (
          <Skeleton />
        ) : isError ? (
          <ErrorState message={(error as Error)?.message ?? "Unknown error"} />
        ) : !data || data.length === 0 ? (
          <EmptyState />
        ) : (
          <ChatsList rows={data} />
        )}
      </div>
    </div>
  );
}

function ChatsList({ rows }: { rows: Awaited<ReturnType<typeof listConversations>> }) {
  return (
    <ul className="divide-y divide-zinc-100" data-testid="chats-list">
      {rows.map((row) => (
        <li key={row.id} className="hover:bg-zinc-50">
          <Link
            to={`/chats/${row.id}`}
            className="block px-4 py-3"
            data-testid={`chat-link-${row.id}`}
          >
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate font-medium text-zinc-900">{row.title}</span>
              <span
                className="whitespace-nowrap text-xs text-zinc-500"
                title={row.updated_at}
              >
                {relativeTime(row.updated_at)}
              </span>
            </div>
            <div className="mt-1 flex items-center gap-2 text-xs text-zinc-500">
              <span className="rounded bg-zinc-100 px-1.5 py-0.5 font-mono">
                {row.model}
              </span>
              <span>
                {row.message_count} message{row.message_count === 1 ? "" : "s"}
              </span>
            </div>
            {row.preview && (
              <p className="mt-1 truncate text-sm text-zinc-600">{row.preview}</p>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
}

function EmptyState() {
  return (
    <div className="p-12 text-center" data-testid="empty-state">
      <h2 className="text-lg font-medium text-zinc-900">No chats yet</h2>
      <p className="mt-2 text-sm text-zinc-600">
        Start a new chat to ask Claude about your catalogs or run an exploratory query.
      </p>
    </div>
  );
}

function Skeleton() {
  return (
    <div className="space-y-3 p-6" data-testid="loading-state">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-12 animate-pulse rounded bg-zinc-100" />
      ))}
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  return (
    <div className="p-8 text-center" data-testid="error-state">
      <h2 className="text-base font-medium text-red-700">Couldn't load chats</h2>
      <p className="mt-2 text-sm text-zinc-600">{message}</p>
      <p className="mt-3 text-xs text-zinc-500">
        Make sure the AI service is running on{" "}
        <code className="rounded bg-zinc-100 px-1.5 py-0.5">VITE_AI_URL</code>.
      </p>
    </div>
  );
}
