"use client";

import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/components/AuthContext";

type Props = {
  onToggleDrawer: () => void;
  onOpenLogin: () => void;
};

export function TopNav({ onToggleDrawer, onOpenLogin }: Props) {
  const { user, logout } = useAuth();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function handler(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    window.addEventListener("mousedown", handler);
    return () => window.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  return (
    <header className="grid grid-cols-3 items-center w-full px-6 py-3 bg-white/80 backdrop-blur-md shadow-sm shrink-0 z-50 font-headline font-semibold tracking-tight">
      <div className="flex items-center gap-4">
        <button
          onClick={onToggleDrawer}
          className="p-2 hover:bg-slate-100/50 rounded-lg text-slate-500 transition-colors"
          aria-label="Open recent files drawer"
        >
          <span className="material-symbols-outlined">menu</span>
        </button>
        <div className="text-xl font-bold bg-gradient-to-br from-blue-700 to-blue-500 bg-clip-text text-transparent">
          LexisAI Architect
        </div>
      </div>
      <div className="text-sm font-medium text-blue-700 text-center border-b-2 border-blue-600 pb-1 justify-self-center">
        Workspace
      </div>
      <div className="flex items-center gap-2 justify-self-end">
        <button className="p-2 hover:bg-slate-100/50 rounded-lg text-slate-500 transition-colors">
          <span className="material-symbols-outlined">notifications</span>
        </button>
        <button className="p-2 hover:bg-slate-100/50 rounded-lg text-slate-500 transition-colors">
          <span className="material-symbols-outlined">settings</span>
        </button>
        {user ? (
          <div ref={menuRef} className="relative ml-2">
            <button
              onClick={() => setMenuOpen((o) => !o)}
              className="w-8 h-8 rounded-full bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center text-white text-xs font-semibold hover:opacity-90"
              aria-label={`Logged in as ${user.username}`}
            >
              {user.username.slice(0, 2).toUpperCase()}
            </button>
            {menuOpen && (
              <div className="absolute right-0 mt-2 w-44 bg-surface-container-lowest rounded-lg ambient-shadow border border-outline-variant/30 py-1 z-50">
                <div className="px-3 py-2 text-xs text-on-surface-variant border-b border-outline-variant/30">
                  Signed in as
                  <div className="text-sm font-medium text-on-surface truncate">
                    {user.username}
                  </div>
                </div>
                <button
                  onClick={async () => {
                    setMenuOpen(false);
                    await logout();
                  }}
                  className="w-full text-left px-3 py-2 text-sm text-on-surface hover:bg-surface-container-low"
                >
                  Log out
                </button>
              </div>
            )}
          </div>
        ) : (
          <button
            onClick={onOpenLogin}
            className="ml-2 px-3 py-1.5 text-sm font-medium text-blue-700 border border-blue-200 rounded-lg hover:bg-blue-50 transition-colors"
          >
            Sign in
          </button>
        )}
      </div>
    </header>
  );
}
