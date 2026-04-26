<!DOCTYPE html>

<html class="light" lang="en"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>LexisAI Architect - Document Analysis</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&amp;family=Inter:wght@400;500;600&amp;display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&amp;display=swap" rel="stylesheet"/>
<script id="tailwind-config">
        tailwind.config = {
            darkMode: "class",
            theme: {
                extend: {
                    "colors": {
                        "secondary-fixed-dim": "#b4c5ff",
                        "on-error": "#ffffff",
                        "tertiary-fixed": "#ffdbcd",
                        "on-primary-fixed": "#00174b",
                        "on-tertiary-fixed-variant": "#7d2d00",
                        "background": "#f8f9ff",
                        "primary-fixed": "#dbe1ff",
                        "surface-bright": "#f8f9ff",
                        "surface-container-lowest": "#ffffff",
                        "on-error-container": "#93000a",
                        "on-primary": "#ffffff",
                        "on-background": "#0b1c30",
                        "on-secondary": "#ffffff",
                        "on-tertiary-fixed": "#360f00",
                        "surface-tint": "#0053db",
                        "on-primary-fixed-variant": "#003ea8",
                        "on-surface": "#0b1c30",
                        "surface-container-low": "#eff4ff",
                        "primary": "#004ac6",
                        "on-tertiary": "#ffffff",
                        "secondary-fixed": "#dbe1ff",
                        "error-container": "#ffdad6",
                        "surface-container-high": "#dce9ff",
                        "outline": "#737686",
                        "inverse-primary": "#b4c5ff",
                        "primary-container": "#2563eb",
                        "on-primary-container": "#eeefff",
                        "secondary": "#495c95",
                        "on-secondary-fixed": "#00174b",
                        "surface-container-highest": "#d3e4fe",
                        "on-tertiary-container": "#ffede6",
                        "outline-variant": "#c3c6d7",
                        "inverse-on-surface": "#eaf1ff",
                        "primary-fixed-dim": "#b4c5ff",
                        "tertiary-container": "#bc4800",
                        "surface-variant": "#d3e4fe",
                        "surface-dim": "#cbdbf5",
                        "on-surface-variant": "#434655",
                        "on-secondary-container": "#394c84",
                        "surface-container": "#e5eeff",
                        "error": "#ba1a1a",
                        "surface": "#f8f9ff",
                        "tertiary": "#943700",
                        "tertiary-fixed-dim": "#ffb596",
                        "secondary-container": "#acbfff",
                        "on-secondary-fixed-variant": "#31447b",
                        "inverse-surface": "#213145"
                    },
                    "borderRadius": {
                        "DEFAULT": "0.125rem",
                        "lg": "0.25rem",
                        "xl": "0.5rem",
                        "full": "0.75rem"
                    },
                    "fontFamily": {
                        "headline": ["Manrope", "sans-serif"],
                        "body": ["Inter", "sans-serif"],
                        "label": ["Inter", "sans-serif"]
                    }
                }
            }
        }
    </script>
<style>
        .glass-panel {
            background-color: rgba(248, 249, 255, 0.8);
            backdrop-filter: blur(20px);
        }
        .gradient-primary {
            background: linear-gradient(135deg, #004ac6, #2563eb);
        }
        .ambient-shadow {
            box-shadow: 0 8px 40px -8px rgba(11, 28, 48, 0.06);
        }
        /* Custom Scrollbar for inner elements */
        .custom-scroll::-webkit-scrollbar {
            width: 6px;
        }
        .custom-scroll::-webkit-scrollbar-track {
            background: transparent;
        }
        .custom-scroll::-webkit-scrollbar-thumb {
            background-color: rgba(115, 118, 134, 0.2);
            border-radius: 10px;
        }
        body { margin: 0; padding: 0; height: 100vh; overflow: hidden; }
    </style>
</head>
<body class="bg-background text-on-background font-body antialiased flex flex-col h-screen">
<!-- TopNavBar -->
<header class="flex justify-between items-center w-full px-6 py-3 bg-white/80 dark:bg-slate-900/80 backdrop-blur-md font-manrope font-semibold tracking-tight docked full-width top-0 z-50 no-line boundary-by-tone bg-slate-50 dark:bg-slate-950 shadow-sm dark:shadow-none transition-all duration-200 ease-in-out active:scale-95 text-blue-600 dark:text-blue-400 shrink-0">
<div class="flex items-center gap-4">
<button class="p-2 hover:bg-slate-100/50 dark:hover:bg-slate-800/50 rounded-lg text-slate-500 dark:text-slate-400 transition-colors">
<span class="material-symbols-outlined">menu</span>
</button>
<div class="text-xl font-bold bg-gradient-to-br from-blue-700 to-blue-500 bg-clip-text text-transparent">LexisAI Architect</div>
</div>
<nav class="hidden md:flex gap-6">
<a class="text-blue-700 dark:text-blue-400 border-b-2 border-blue-600 dark:border-blue-400 pb-1" href="#">Workspace</a>
<a class="text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition-colors hover:bg-slate-100/50 dark:hover:bg-slate-800/50 rounded-lg px-2 py-1" href="#">Analytics</a>
<a class="text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition-colors hover:bg-slate-100/50 dark:hover:bg-slate-800/50 rounded-lg px-2 py-1" href="#">Library</a>
<a class="text-slate-500 dark:text-slate-400 hover:text-slate-900 dark:hover:text-white transition-colors hover:bg-slate-100/50 dark:hover:bg-slate-800/50 rounded-lg px-2 py-1" href="#">Team</a>
</nav>
<div class="flex items-center gap-2">
<button class="p-2 hover:bg-slate-100/50 dark:hover:bg-slate-800/50 rounded-lg text-slate-500 dark:text-slate-400 transition-colors">
<span class="material-symbols-outlined" data-icon="notifications">notifications</span>
</button>
<button class="p-2 hover:bg-slate-100/50 dark:hover:bg-slate-800/50 rounded-lg text-slate-500 dark:text-slate-400 transition-colors">
<span class="material-symbols-outlined" data-icon="settings">settings</span>
</button>
<img alt="User profile" class="w-8 h-8 rounded-full ml-2" src="https://lh3.googleusercontent.com/aida-public/AB6AXuCKBb2h6T_hHqEdhpVK5XgUjxszRsf2FuwGewcdw8j5cf5iIhIL8qngFaxE-3mperNv8UuESrQ3-4DV6sXXYsaWq0bsXfZd4nfFWzUOCfKa9P_q1jzaojoGwYe2n8JqP8DoCBupfXqqWT_ThtRaXlISV_NO_qZvFbxgUF05Pyv41vCYmsjROMoi0ThbJGgqvYYwcS-A3Od14vWVPyhDMoqVj6IfUz9S1yzd5WctWbXPwzueORXWTuUXDQTy2c9q4LrX-BhlF-cgE2k"/>
</div>
</header>
<!-- Main Workspace - Three Column Split -->
<main class="flex-1 flex overflow-hidden w-full max-w-[1920px] mx-auto p-4 gap-6">
<!-- Column 1: Document Viewer (50%) -->
<section class="w-1/2 flex flex-col bg-surface-container rounded-lg ambient-shadow overflow-hidden relative">
<!-- Doc Viewer Toolbar -->
<div class="flex items-center justify-between p-4 bg-surface-container-low z-10">
<div class="flex items-center gap-3">
<span class="material-symbols-outlined text-on-surface-variant">description</span>
<h2 class="font-headline font-semibold text-on-surface text-lg">Q3_Financial_Report_Final.pdf</h2>
</div>
<div class="flex gap-2">
<button class="p-1.5 rounded-full hover:bg-surface-container-highest text-on-surface-variant transition-colors"><span class="material-symbols-outlined text-sm">remove</span></button>
<span class="text-sm font-medium text-on-surface-variant py-1.5">100%</span>
<button class="p-1.5 rounded-full hover:bg-surface-container-highest text-on-surface-variant transition-colors"><span class="material-symbols-outlined text-sm">add</span></button>
<div class="w-px h-6 bg-outline-variant/30 mx-2 self-center"></div>
<button class="p-1.5 rounded-full hover:bg-surface-container-highest text-on-surface-variant transition-colors"><span class="material-symbols-outlined text-sm">search</span></button>
</div>
</div>
<!-- PDF Container -->
<div class="flex-1 overflow-y-auto custom-scroll p-6 bg-surface-container-low flex justify-center">
<div class="bg-surface-container-lowest w-full max-w-2xl min-h-[1000px] shadow-sm rounded-md p-10 font-serif text-on-surface relative">
<h1 class="text-3xl font-bold mb-6">Quarterly Financial Review</h1>
<p class="mb-4 text-on-surface-variant leading-relaxed">This document outlines the financial performance for the third quarter of the fiscal year 2023. The scope includes revenue generation, operational expenditures, and strategic investments.</p>
<h3 class="text-xl font-semibold mt-8 mb-3">1. Revenue Streams</h3>
<p class="mb-4 leading-relaxed">Total consolidated revenue for Q3 reached $45.2 million, representing a <span class="bg-primary/20 text-primary-container px-1 rounded">14% year-over-year increase</span>. This growth was primarily driven by the expansion of the enterprise software division.</p>
<h3 class="text-xl font-semibold mt-8 mb-3">2. Operational Expenditures</h3>
<p class="mb-4 leading-relaxed">Operating expenses were tightly managed, coming in at $28.5 million. The <span class="bg-primary/20 text-primary-container px-1 rounded">reduction in overhead costs</span> was offset by increased R&amp;D investments in AI technologies.</p>
<!-- Faux text for length -->
<div class="opacity-50 mt-8 space-y-4 text-sm">
<p class="h-4 bg-surface-container rounded w-full"></p>
<p class="h-4 bg-surface-container rounded w-5/6"></p>
<p class="h-4 bg-surface-container rounded w-4/6"></p>
<p class="h-4 bg-surface-container rounded w-full mt-6"></p>
<p class="h-4 bg-surface-container rounded w-full"></p>
<p class="h-4 bg-surface-container rounded w-3/6"></p>
</div>
</div>
</div>
</section>
<!-- Column 2: Search Process & Results (25%) -->
<section class="w-1/4 flex flex-col gap-6 overflow-hidden">
<!-- Search Reasoning Visualizer -->
<div class="bg-surface-container-lowest rounded-lg p-5 ambient-shadow shrink-0 relative overflow-hidden">
<div class="absolute top-0 left-0 w-1 h-full gradient-primary"></div>
<h3 class="font-headline font-semibold text-on-surface mb-4 flex items-center gap-2">
<span class="material-symbols-outlined text-primary text-sm">psychology</span>
                    Analysis Engine
                </h3>
<div class="space-y-3 font-label text-sm">
<div class="flex items-start gap-3">
<span class="material-symbols-outlined text-primary text-[16px] mt-0.5">check_circle</span>
<div>
<span class="text-on-surface font-medium block">Scanning document context</span>
<span class="text-on-surface-variant text-[11px]">Identified 45 pages related to "Q3 Financials"</span>
</div>
</div>
<div class="w-px h-4 bg-outline-variant/30 ml-2 mt-[-4px] mb-[-4px]"></div>
<div class="flex items-start gap-3">
<span class="material-symbols-outlined text-primary text-[16px] mt-0.5">filter_alt</span>
<div>
<span class="text-on-surface font-medium block">Filtering semantic matches</span>
<span class="text-on-surface-variant text-[11px]">Found 12 relevant snippets for "growth metrics"</span>
</div>
</div>
<div class="w-px h-4 bg-outline-variant/30 ml-2 mt-[-4px] mb-[-4px]"></div>
<div class="flex items-start gap-3">
<div class="w-4 h-4 rounded-full border-2 border-primary border-t-transparent animate-spin mt-0.5"></div>
<div>
<span class="text-primary font-medium block">Synthesizing findings</span>
<span class="text-on-surface-variant text-[11px]">Generating summary...</span>
</div>
</div>
</div>
</div>
<!-- Snippet Results List -->
<div class="flex-1 overflow-y-auto custom-scroll pr-2 flex flex-col gap-4">
<h3 class="font-headline font-semibold text-on-surface sticky top-0 bg-background/90 backdrop-blur-sm py-2 z-10 flex justify-between items-center">
                    Extracted Context
                    <span class="bg-surface-container text-on-surface-variant text-xs px-2 py-0.5 rounded-full">3 Matches</span>
</h3>
<!-- Snippet Card Active -->
<div class="bg-surface-container-lowest border border-primary/20 rounded-lg p-4 cursor-pointer relative shadow-sm ring-1 ring-primary/10">
<div class="absolute left-0 top-1/2 -translate-y-1/2 w-1 h-8 bg-primary rounded-r-md"></div>
<div class="flex justify-between items-start mb-2">
<span class="text-[10px] font-bold text-primary tracking-wider uppercase">Page 3 • Revenue</span>
<div class="flex gap-1">
<button class="text-on-surface-variant hover:text-primary"><span class="material-symbols-outlined text-[14px]">content_copy</span></button>
</div>
</div>
<p class="font-body text-sm text-on-surface leading-relaxed line-clamp-3">
                        "...Total consolidated revenue for Q3 reached $45.2 million, representing a <span class="bg-primary/10 text-primary font-medium px-1 rounded">14% year-over-year increase</span>..."
                    </p>
</div>
<!-- Snippet Card Inactive -->
<div class="bg-surface-container-low hover:bg-surface-container-lowest rounded-lg p-4 cursor-pointer transition-colors duration-200">
<div class="flex justify-between items-start mb-2">
<span class="text-[10px] font-bold text-on-surface-variant tracking-wider uppercase">Page 5 • Expenditures</span>
</div>
<p class="font-body text-sm text-on-surface-variant leading-relaxed line-clamp-3">
                        "...The <span class="text-on-surface font-medium">reduction in overhead costs</span> was offset by increased R&amp;D investments in AI technologies, signaling a pivot toward..."
                    </p>
</div>
<!-- Snippet Card Inactive -->
<div class="bg-surface-container-low hover:bg-surface-container-lowest rounded-lg p-4 cursor-pointer transition-colors duration-200">
<div class="flex justify-between items-start mb-2">
<span class="text-[10px] font-bold text-on-surface-variant tracking-wider uppercase">Page 12 • Projections</span>
</div>
<p class="font-body text-sm text-on-surface-variant leading-relaxed line-clamp-3">
                        "...Forward-looking growth metrics indicate an expected <span class="text-on-surface font-medium">8% to 10% acceleration</span> in the final quarter..."
                    </p>
</div>
</div>
</section>
<!-- Column 3: Input & Summary (25%) -->
<section class="w-1/4 flex flex-col gap-6 overflow-hidden">
<!-- Search Input Area -->
<div class="bg-surface-container-lowest rounded-lg p-5 ambient-shadow shrink-0 relative group">
<label class="font-headline font-semibold text-on-surface block mb-3 text-sm" for="ai-query">Ask LexisAI</label>
<div class="relative">
<textarea class="w-full bg-surface-container-low border-none rounded-md p-4 text-sm text-on-surface placeholder:text-outline-variant focus:ring-1 focus:ring-primary/20 transition-all custom-scroll resize-none" id="ai-query" placeholder="E.g., Summarize the primary growth metrics and overhead cost reductions mentioned in Q3." rows="3"></textarea>
<div class="absolute bottom-3 right-3 flex gap-2">
<button class="p-1.5 text-on-surface-variant hover:text-primary transition-colors"><span class="material-symbols-outlined text-[18px]">mic</span></button>
</div>
</div>
<div class="mt-4 flex justify-between items-center">
<div class="flex gap-2">
<span class="bg-surface-container text-on-surface-variant text-[10px] px-2 py-1 rounded-md uppercase tracking-wide font-semibold cursor-pointer hover:bg-surface-container-highest transition-colors">Growth</span>
<span class="bg-surface-container text-on-surface-variant text-[10px] px-2 py-1 rounded-md uppercase tracking-wide font-semibold cursor-pointer hover:bg-surface-container-highest transition-colors">Costs</span>
</div>
<button class="gradient-primary text-white text-sm font-medium px-4 py-2 rounded-xl flex items-center gap-2 hover:opacity-90 transition-opacity">
                        Analyze
                        <span class="material-symbols-outlined text-[16px]">arrow_forward</span>
</button>
</div>
</div>
<!-- AI Summary Card -->
<div class="flex-1 bg-surface-container-lowest rounded-lg p-6 ambient-shadow overflow-y-auto custom-scroll flex flex-col">
<div class="flex items-center gap-3 mb-6">
<div class="w-8 h-8 rounded-full gradient-primary flex items-center justify-center text-white shrink-0">
<span class="material-symbols-outlined text-sm">auto_awesome</span>
</div>
<h3 class="font-headline font-semibold text-on-surface text-lg">Executive Summary</h3>
</div>
<div class="prose prose-sm prose-slate dark:prose-invert font-body text-on-surface flex-1">
<p class="leading-relaxed mb-4">
                        Based on the analysis of the <strong>Q3 Financial Report</strong>, the primary growth metrics indicate strong performance in revenue generation, specifically a <span class="bg-surface-container-high px-1 rounded text-primary-container font-medium">14% YoY increase ($45.2M)</span> driven by enterprise software expansion.
                    </p>
<p class="leading-relaxed mb-4">
                        Concurrently, overhead costs were actively reduced. Operating expenses landed at $28.5M. The savings from overhead reductions were reallocated toward <strong>R&amp;D investments in AI technologies</strong>.
                    </p>
<div class="mt-6 p-4 bg-surface-container-low rounded-lg border-l-2 border-primary">
<h4 class="text-sm font-semibold mb-2 flex items-center gap-2">
<span class="material-symbols-outlined text-[14px] text-primary">lightbulb</span>
                            Key Takeaway
                        </h4>
<p class="text-xs text-on-surface-variant leading-relaxed">
                            The company is successfully balancing top-line growth with operational efficiency, prioritizing future-proofing investments over short-term margin maximization.
                        </p>
</div>
</div>
<div class="mt-6 pt-4 border-t border-outline-variant/20 flex justify-end gap-3">
<button class="text-sm font-medium text-on-surface-variant hover:text-primary transition-colors flex items-center gap-1">
<span class="material-symbols-outlined text-[16px]">thumb_up</span> Helpful
                    </button>
<button class="text-sm font-medium text-on-surface-variant hover:text-primary transition-colors flex items-center gap-1">
<span class="material-symbols-outlined text-[16px]">content_copy</span> Copy
                    </button>
</div>
</div>
</section>
</main>
</body></html>
