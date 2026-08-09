import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { createConversation, listConversations } from "@/api/conversations";
import { ChatConversation } from "@/components/ChatConversation";
import { relativeTime } from "@/lib/format";

/**
 * Always-present left rail. Two modes, driven by local state (not the route):
 * a list of chats, or a single chat opened in-panel. "Expand" jumps to the
 * full-screen route; the panel resets to its list so there's never a second
 * live copy of the same conversation.
 */
export function ChatSidePanel() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const navigate = useNavigate();

  return (
    <aside
      className="flex w-80 shrink-0 flex-col border-r border-zinc-200 bg-white"
      data-testid="chat-side-panel"
    >
      {selectedId ? (
        <div className="flex-1 overflow-hidden p-3">
          <ChatConversation
            key={selectedId}
            id={selectedId}
            variant="panel"
            onBack={() => setSelectedId(null)}
            onExpand={() => {
              const id = selectedId;
              setSelectedId(null);
              navigate(`/chats/${id}`);
            }}
            onDeleted={() => setSelectedId(null)}
          />
        </div>
      ) : (
        <ChatList
          onOpen={(id) => setSelectedId(id)}
          onExpandAll={() => navigate("/chats")}
        />
      )}
    </aside>
  );
}

function ChatList({
  onOpen,
  onExpandAll,
}: {
  onOpen: (id: string) => void;
  onExpandAll: () => void;
}) {
  const qc = useQueryClient();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["conversations"],
    queryFn: listConversations,
  });

  const newChat = useMutation({
    mutationFn: () => createConversation({}),
    onSuccess: async (conv) => {
      await qc.invalidateQueries({ queryKey: ["conversations"] });
      onOpen(conv.id);
    },
  });

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-2 border-b border-zinc-100 px-4 py-3">
        <h2 className="text-sm font-semibold tracking-tight">Chats</h2>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => newChat.mutate()}
            disabled={newChat.isPending}
            className="rounded bg-zinc-900 px-2 py-1 text-xs font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50"
            data-testid="new-chat-button"
          >
            {newChat.isPending ? "…" : "New"}
          </button>
          <button
            type="button"
            onClick={onExpandAll}
            className="rounded p-1 text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800"
            title="Open chats full screen"
            aria-label="Open chats full screen"
            data-testid="chats-expand-button"
          >
            <ExpandIcon />
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {isLoading ? (
          <ListSkeleton />
        ) : isError ? (
          <ListError message={(error as Error)?.message ?? "Unknown error"} />
        ) : !data || data.length === 0 ? (
          <Empty onNew={() => newChat.mutate()} pending={newChat.isPending} />
        ) : (
          <ul className="divide-y divide-zinc-100" data-testid="chats-list">
            {data.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  onClick={() => onOpen(row.id)}
                  className="block w-full px-4 py-3 text-left hover:bg-zinc-50"
                  data-testid={`chat-link-${row.id}`}
                >
                  <div className="flex items-baseline justify-between gap-2">
                    <span className="truncate text-sm font-medium text-zinc-900">
                      {row.title}
                    </span>
                    <span
                      className="shrink-0 whitespace-nowrap text-[11px] text-zinc-500"
                      title={row.updated_at}
                    >
                      {relativeTime(row.updated_at)}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-1.5 text-[11px] text-zinc-500">
                    <span className="rounded bg-zinc-100 px-1 py-0.5 font-mono">
                      {row.model}
                    </span>
                    <span>
                      {row.message_count} msg{row.message_count === 1 ? "" : "s"}
                    </span>
                  </div>
                  {row.preview && (
                    <p className="mt-1 line-clamp-2 text-xs text-zinc-600">
                      {row.preview}
                    </p>
                  )}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ExpandIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="currentColor" aria-hidden="true" className="h-4 w-4">
      <path d="M13 3a1 1 0 0 0 0 2h1.586l-4.293 4.293a1 1 0 0 0 1.414 1.414L16 6.414V8a1 1 0 1 0 2 0V4a1 1 0 0 0-1-1h-4ZM7 17a1 1 0 1 0 0-2H5.414l4.293-4.293a1 1 0 0 0-1.414-1.414L4 13.586V12a1 1 0 1 0-2 0v4a1 1 0 0 0 1 1h4Z" />
    </svg>
  );
}

function Empty({ onNew, pending }: { onNew: () => void; pending: boolean }) {
  return (
    <div className="p-6 text-center" data-testid="empty-state">
      <p className="text-sm text-zinc-600">No chats yet.</p>
      <button
        type="button"
        onClick={onNew}
        disabled={pending}
        className="mt-3 rounded bg-zinc-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
      >
        {pending ? "Creating…" : "Start a chat"}
      </button>
    </div>
  );
}

function ListSkeleton() {
  return (
    <div className="space-y-3 p-4" data-testid="loading-state">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-12 animate-pulse rounded bg-zinc-100" />
      ))}
    </div>
  );
}

function ListError({ message }: { message: string }) {
  return (
    <div className="p-4 text-center" data-testid="error-state">
      <h2 className="text-sm font-medium text-red-700">Couldn't load chats</h2>
      <p className="mt-1 text-xs text-zinc-600">{message}</p>
      <p className="mt-2 text-[11px] text-zinc-500">
        Make sure the AI service is running on{" "}
        <code className="rounded bg-zinc-100 px-1 py-0.5">VITE_AI_URL</code>.
      </p>
    </div>
  );
}
