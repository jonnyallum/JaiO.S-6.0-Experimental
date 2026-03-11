"""
UI / Visual Designer - 19-point @langraph compliant agent node.

Node Contract:
    Inputs : task (str), design_context (str), output_type (VALID_OUTPUT_TYPES), component_type (VALID_COMPONENT_TYPES)
    Outputs: design_spec (str), component_code (str)
    Side-FX: CallMetrics persisted to DB

Loop Policy:
    MAX_RETRIES = 3 - retries on TRANSIENT (API overload) only.
    Permanent failures (empty task, invalid output_type) raise immediately.

Failure Discrimination:
    PERMANENT  → empty task, unknown output_type/component_type → ValueError (no retry)
    TRANSIENT  → HTTP 529 / APIStatusError overload → retried up to MAX_RETRIES
    UNEXPECTED → all other exceptions → re-raised with context

Checkpoint Semantics:
    PRE  - state snapshot before design spec generation
    POST - design_spec + component_code persisted after successful generation
"""

from __future__ import annotations

import re
from typing import TypedDict

import anthropic
from anthropic import APIStatusError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from personas.config import get_persona
from utils.metrics import CallMetrics
from utils.checkpoints import checkpoint

ROLE        = "ui_designer"
MAX_RETRIES = 3
MAX_TOKENS  = 2800

VALID_OUTPUT_TYPES = {
    "component_spec", "landing_page", "dashboard_layout", "design_system",
    "animation_spec", "mobile_screen", "design_review", "general",
}
VALID_COMPONENT_TYPES = {
    "hero", "navbar", "card", "form", "modal", "table", "dashboard",
    "pricing", "testimonial", "cta", "footer", "sidebar", "general",
}

# ── Design System Reference ────────────────────────────────────────────────────
_TAILWIND_PATTERNS = {
    "spacing":     "space-y-4, gap-6, p-8 - use 4-unit increments",
    "typography":  "text-4xl font-bold tracking-tight / text-muted-foreground text-sm",
    "shadows":     "shadow-sm (cards) / shadow-lg (modals) / shadow-none (flat)",
    "radii":       "rounded-lg (cards) / rounded-full (badges/avatars) / rounded-xl (modals)",
    "motion":      "transition-all duration-200 ease-out for hover states",
    "dark_mode":   "dark: prefix - always pair light/dark variants",
    "responsive":  "mobile-first: base → sm: → md: → lg: → xl:",
}

_FRAMER_MOTION_PATTERNS = {
    "fade_in":     "initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}",
    "slide_up":    "initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}",
    "stagger":     "variants with staggerChildren: 0.1 on container",
    "hover_lift":  "whileHover={{ y: -4, scale: 1.02 }} transition={{ type: 'spring', stiffness: 400 }}",
    "page_trans":  "AnimatePresence with mode='wait' for route transitions",
}

_DESIGN_PRINCIPLES = [
    "8-point grid - all spacing multiples of 8px (Tailwind: 2, 4, 6, 8...)",
    "3-colour rule - primary, neutral, accent only (+ semantic: error/success)",
    "Typography scale: 4 sizes max per view - do not mix too many weights",
    "One clear CTA per section - no competing primary actions",
    "White space is not wasted space - breathing room = clarity",
    "Mobile-first layout - test at 375px before desktop",
    "Accessible contrast: 4.5:1 minimum for body text, 3:1 for large text",
    "Micro-interactions on every interactive element - 200ms hover state",
]

_COMPONENT_SPECS = {
    "hero": {
        "structure":  "Headline → Subheadline → CTA pair → Social proof / media",
        "animation":  "Fade-in headline, slide-up subheadline, delayed CTA",
        "common_mistakes": ["Headline too vague", "Two equal-weight CTAs", "No above-fold social proof"],
    },
    "card": {
        "structure":  "Image → Badge → Title → Body → Action",
        "animation":  "Hover lift (y: -4) + subtle shadow increase",
        "common_mistakes": ["Inconsistent padding", "No hover state", "Text overflows on small screens"],
    },
    "form": {
        "structure":  "Label → Input → Helper text → Error state → Submit",
        "animation":  "Input focus ring, error shake animation",
        "common_mistakes": ["Missing error states", "No loading state on submit", "Label placeholder confusion"],
    },
    "dashboard": {
        "structure":  "Sidebar nav → Header with actions → KPI cards → Charts → Tables",
        "animation":  "Skeleton loaders, counter animations for KPIs",
        "common_mistakes": ["Too much data density", "No empty states", "Missing loading states"],
    },
    "general": {
        "structure":  "Context-dependent",
        "animation":  "Fade-in default",
        "common_mistakes": ["Inconsistent spacing", "No interactive states", "Missing accessibility"],
    },
}


class UiDesignerState(TypedDict, total=False):
    workflow_id:    str
    timestamp:      str
    agent:          str
    error:          str | None
    task:           str
    design_context: str
    output_type:    str
    component_type: str
    design_spec:    str
    component_code: str


# ── Phase 1 - Design Analysis (pure, no Claude) ───────────────────────────────
def _analyse_design_requirements(task: str, component_type: str) -> dict:
    """Returns design_data dict - pure lookup, no Claude."""
    comp_spec  = _COMPONENT_SPECS.get(component_type, _COMPONENT_SPECS["general"])
    task_lower = task.lower()
    flags: list[str] = []

    if "dark" in task_lower or "theme" in task_lower:
        flags.append("Dark mode required - use CSS variables + Tailwind dark: variants")
    if "animation" in task_lower or "motion" in task_lower:
        flags.append("Framer Motion required - import from framer-motion, not @/lib")
    if "mobile" in task_lower or "responsive" in task_lower:
        flags.append("Mobile-first - design 375px breakpoint first")
    if "accessible" in task_lower or "a11y" in task_lower or "wcag" in task_lower:
        flags.append("Accessibility required - aria-labels, focus rings, keyboard navigation")
    if "loading" in task_lower or "skeleton" in task_lower:
        flags.append("Skeleton loading state required - never show empty containers")

    return {
        "component_spec":      comp_spec,
        "tailwind_patterns":   _TAILWIND_PATTERNS,
        "motion_patterns":     _FRAMER_MOTION_PATTERNS,
        "design_principles":   _DESIGN_PRINCIPLES,
        "flags":               flags,
    }

_build_prompt = None  # assigned below


# ── Phase 2 - Claude Design Spec ───────────────────────────────────────────────
def _build_design_prompt(state: UiDesignerState, design_data: dict) -> str:
    persona    = get_persona(ROLE)
    task       = state["task"]
    ctx        = state.get("design_context", "")
    out_type   = state.get("output_type", "component_spec")
    comp_type  = state.get("component_type", "general")
    comp_spec  = design_data["component_spec"]

    flags_text      = "\n".join(f"  ⚡ {f}" for f in design_data["flags"]) or "  None detected"
    principles_text = "\n".join(f"  • {p}" for p in design_data["design_principles"])
    mistakes_text   = "\n".join(f"  ✗ {m}" for m in comp_spec["common_mistakes"])

    motion_text = "\n".join(f"  {k}: {v}" for k, v in design_data["motion_patterns"].items())

    return f"""You are {persona['name']} ({persona['nickname']}), a {persona['personality']} specialist.

MISSION: Design a god-tier {out_type} for component type: {comp_type}.

COMPONENT SPEC:
  Structure:  {comp_spec['structure']}
  Animation:  {comp_spec['animation']}

COMMON MISTAKES TO AVOID:
{mistakes_text}

TAILWIND PATTERNS:
  Spacing:    {design_data['tailwind_patterns']['spacing']}
  Typography: {design_data['tailwind_patterns']['typography']}
  Motion:     {design_data['tailwind_patterns']['motion']}

FRAMER MOTION REFERENCE:
{motion_text}

DESIGN PRINCIPLES:
{principles_text}

DESIGN FLAGS:
{flags_text}

TASK:
{task}

DESIGN CONTEXT / BRAND:
{ctx or "None provided - use clean, modern defaults"}

OUTPUT FORMAT:
## UI Design Spec: {out_type.replace('_',' ').title()} - {comp_type}

### Design Decisions
[3–5 key choices with rationale - colour, layout, motion, typography]

### Component Code
```tsx
// Production-ready React + Tailwind + Framer Motion
// Full component - no placeholders, no TODO comments
```

### Responsive Behaviour
[Mobile → tablet → desktop - specific breakpoint changes]

### States
[Default / Hover / Active / Focus / Loading / Empty / Error - describe each]

### Accessibility
[aria attributes, keyboard nav, focus management, contrast ratios]

### Animation Spec
[Timing, easing, trigger, what property animates - reference Framer Motion]

### Next Action
[Single most important first step]
"""

_build_prompt = _build_design_prompt  # spec alias


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, APIStatusError) and exc.status_code in (429, 529)


@retry(stop=stop_after_attempt(MAX_RETRIES), wait=wait_exponential(multiplier=1, min=2, max=30),
       retry=retry_if_exception(_is_transient), reraise=True)
def _generate(client: anthropic.Anthropic, prompt: str, metrics: CallMetrics) -> str:
    metrics.start()
    response = client.messages.create(model="claude-opus-4-6", max_tokens=MAX_TOKENS,
                                       messages=[{"role": "user", "content": prompt}])
    metrics.record(response); metrics.log(); metrics.persist()
    return response.content[0].text


def ui_designer_node(state: UiDesignerState) -> UiDesignerState:
    thread_id  = state.get("workflow_id", "local")
    task       = state.get("task", "").strip()
    out_type   = state.get("output_type", "component_spec")
    comp_type  = state.get("component_type", "general")

    if not task:
        raise ValueError("PERMANENT: task is required.")
    if out_type not in VALID_OUTPUT_TYPES:
        raise ValueError(f"PERMANENT: output_type '{out_type}' not in {VALID_OUTPUT_TYPES}")
    if comp_type not in VALID_COMPONENT_TYPES:
        raise ValueError(f"PERMANENT: component_type '{comp_type}' not in {VALID_COMPONENT_TYPES}")

    checkpoint("PRE", thread_id, ROLE, {"output_type": out_type, "component_type": comp_type})
    design_data = _analyse_design_requirements(task, comp_type)

    client  = anthropic.Anthropic()
    metrics = CallMetrics(thread_id, ROLE)
    prompt  = _build_design_prompt(state, design_data)

    try:
        spec = _generate(client, prompt, metrics)
    except APIStatusError as exc:
        if exc.status_code in (429, 529): raise
        raise RuntimeError(f"UNEXPECTED: APIStatusError {exc.status_code}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"UNEXPECTED: {type(exc).__name__}: {exc}") from exc

    code_match     = re.search(r'```tsx([\s\S]+?)```', spec)
    component_code = code_match.group(1).strip() if code_match else ""

    checkpoint("POST", thread_id, ROLE, {"output_type": out_type, "component_type": comp_type})

    return {**state, "agent": ROLE, "design_spec": spec, "component_code": component_code, "error": None}
