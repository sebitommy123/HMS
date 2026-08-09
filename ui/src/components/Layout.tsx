import { NavLink, Outlet, useLocation } from "react-router-dom";

import { ChatSidePanel } from "@/components/ChatSidePanel";
import { HealthIndicator } from "@/components/HealthIndicator";
import { cn } from "@/lib/utils";

export function Layout() {
  const { pathname } = useLocation();
  // The /chats routes ARE the full-screen "expanded" chat view — showing the
  // rail there would be a redundant second copy, so hide it. Everywhere else
  // the rail is always present.
  const showChatRail = !(pathname === "/chats" || pathname.startsWith("/chats/"));

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-zinc-50">
      <header className="shrink-0 border-b border-zinc-200 bg-white">
        <div className="flex w-full items-center justify-between px-6 py-3">
          <div className="flex items-center gap-6">
            <span className="text-base font-semibold tracking-tight">DataPro</span>
            <nav className="flex items-center gap-1 text-sm">
              <NavTab to="/">Overview</NavTab>
              <NavTab to="/catalogs">Catalogs</NavTab>
              <NavTab to="/object-types">Object Types</NavTab>
              <NavTab to="/state">State</NavTab>
              <NavTab to="/query">Query</NavTab>
              <NavTab to="/raw-trino-query">Raw Trino</NavTab>
            </nav>
          </div>
          <HealthIndicator />
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        {showChatRail && <ChatSidePanel />}
        <main className="min-w-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-6xl px-6 py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}

function NavTab({ to, children }: { to: string; children: React.ReactNode }) {
  // "end" matching is right for "/" (otherwise it matches every path), but for
  // section roots like "/catalogs" we want the tab to stay active on nested
  // routes like /catalogs/new or /catalogs/:name.
  const exact = to === "/";
  return (
    <NavLink
      to={to}
      end={exact}
      className={({ isActive }) =>
        cn(
          "rounded px-2.5 py-1.5 text-zinc-600 transition hover:text-zinc-900",
          isActive && "bg-zinc-100 text-zinc-900",
        )
      }
    >
      {children}
    </NavLink>
  );
}
