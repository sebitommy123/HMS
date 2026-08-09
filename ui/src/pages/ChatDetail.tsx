import { Link, useNavigate, useParams } from "react-router-dom";

import { ChatConversation } from "@/components/ChatConversation";

/**
 * Full-screen view of a single chat. The streaming machinery lives in the
 * shared <ChatConversation>; this page just gives it a full-height frame and
 * a breadcrumb back to the chats list. The same conversation can also be
 * opened in the always-present side panel.
 */
export function ChatDetail() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();

  return (
    <div className="flex h-[calc(100vh-9rem)] flex-col">
      <nav className="mb-3 text-sm text-zinc-500">
        <Link to="/chats" className="hover:text-zinc-900">
          Chats
        </Link>
        <span className="mx-2">/</span>
        <span className="text-zinc-700">this chat</span>
      </nav>
      <div className="min-h-0 flex-1">
        <ChatConversation
          key={id}
          id={id}
          variant="full"
          onDeleted={() => navigate("/chats")}
        />
      </div>
    </div>
  );
}
