"use client";

type Props = {
  onToggleDrawer: () => void;
};

export function TopNav({ onToggleDrawer }: Props) {
  return (
    <header className="flex justify-between items-center w-full px-6 py-3 bg-white/80 backdrop-blur-md shadow-sm shrink-0 z-50 font-headline font-semibold tracking-tight">
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
      <nav className="hidden md:flex gap-6">
        <a className="text-blue-700 border-b-2 border-blue-600 pb-1" href="#">Workspace</a>
        <a className="text-slate-500 hover:text-slate-900 rounded-lg px-2 py-1" href="#">Analytics</a>
        <a className="text-slate-500 hover:text-slate-900 rounded-lg px-2 py-1" href="#">Library</a>
        <a className="text-slate-500 hover:text-slate-900 rounded-lg px-2 py-1" href="#">Team</a>
      </nav>
      <div className="flex items-center gap-2">
        <button className="p-2 hover:bg-slate-100/50 rounded-lg text-slate-500 transition-colors">
          <span className="material-symbols-outlined">notifications</span>
        </button>
        <button className="p-2 hover:bg-slate-100/50 rounded-lg text-slate-500 transition-colors">
          <span className="material-symbols-outlined">settings</span>
        </button>
        <div className="w-8 h-8 rounded-full ml-2 bg-gradient-to-br from-blue-500 to-blue-700 flex items-center justify-center text-white text-xs font-semibold">
          YOU
        </div>
      </div>
    </header>
  );
}
