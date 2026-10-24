/* Sample data for the review-only prototype. No network access.
   Shape mirrors /api/graph and /api/board closely enough to exercise the
   design: statuses, priorities, derived flags and claim fields.

   Edge A -> B means "B depends on A". */
(function () {
  "use strict";

  window.TACK_PROTOTYPE = {
    project: "tackboard",
    database: "~/.hermes/state/tackboard/tackboard.sqlite3",
    summary: {
      total: 14,
      eligible: 2,
      blocked: 3,
      claimed: 3,
      by_status: {
        backlog: 3,
        ready: 2,
        in_progress: 3,
        review: 1,
        done: 3,
        cancelled: 2
      }
    },
    tasks: [
      {
        id: "T-000038",
        title: "Retire the legacy export script",
        status: "cancelled",
        priority: "low",
        assignee: "",
        labels: ["tooling"],
        claimed_by: null,
        claimed_at: null,
        blocked: false,
        blocked_reason: null,
        eligible: false,
        reviewable: false,
        dependency_ready: true,
        updated_at: "2026-10-20T11:04:00Z"
      },
      {
        id: "T-000039",
        title: "Schema migration for claim timestamps",
        status: "done",
        priority: "normal",
        assignee: "agent-c",
        labels: ["schema"],
        claimed_by: null,
        claimed_at: null,
        blocked: false,
        blocked_reason: null,
        eligible: false,
        reviewable: false,
        dependency_ready: true,
        updated_at: "2026-10-22T08:15:00Z"
      },
      {
        id: "T-000040",
        title: "Claim table indexes",
        status: "in_progress",
        priority: "high",
        assignee: "agent-e",
        labels: ["schema", "performance"],
        claimed_by: "agent-e",
        claimed_at: "2026-10-24T07:12:00Z",
        blocked: false,
        blocked_reason: null,
        eligible: false,
        reviewable: false,
        dependency_ready: true,
        updated_at: "2026-10-24T07:12:00Z"
      },
      {
        id: "T-000041",
        title: "Design system and UX specification for the read-only web view",
        status: "in_progress",
        priority: "high",
        assignee: "agent-a",
        labels: ["web-redesign"],
        claimed_by: "agent-a",
        claimed_at: "2026-10-24T09:37:06Z",
        blocked: false,
        blocked_reason: null,
        eligible: false,
        reviewable: false,
        dependency_ready: true,
        updated_at: "2026-10-24T09:37:06Z"
      },
      {
        id: "T-000042",
        title: "Theme bootstrap without a flash of wrong theme",
        status: "ready",
        priority: "high",
        assignee: "agent-a",
        labels: ["web-redesign"],
        claimed_by: "agent-a",
        claimed_at: "2026-10-24T09:40:00Z",
        blocked: false,
        blocked_reason: null,
        eligible: true,
        reviewable: false,
        dependency_ready: true,
        updated_at: "2026-10-24T09:40:00Z"
      },
      {
        id: "T-000043",
        title: "Drop the legacy export column",
        status: "backlog",
        priority: "low",
        assignee: "",
        labels: ["schema"],
        claimed_by: null,
        claimed_at: null,
        blocked: true,
        blocked_reason: "prerequisite T-000038 was cancelled",
        eligible: false,
        reviewable: false,
        dependency_ready: false,
        updated_at: "2026-10-21T15:20:00Z"
      },
      {
        id: "T-000044",
        title: "Polling and ETag refresh for the board",
        status: "review",
        priority: "normal",
        assignee: "agent-b",
        labels: ["web-redesign", "performance"],
        claimed_by: "agent-b",
        claimed_at: "2026-10-24T08:05:00Z",
        blocked: false,
        blocked_reason: null,
        eligible: false,
        reviewable: true,
        dependency_ready: true,
        updated_at: "2026-10-24T08:40:00Z"
      },
      {
        id: "T-000045",
        title: "Graph pointer navigation overhaul",
        status: "in_progress",
        priority: "urgent",
        assignee: "agent-e",
        labels: ["web-redesign", "graph"],
        claimed_by: "agent-e",
        claimed_at: "2026-10-24T06:30:00Z",
        blocked: true,
        blocked_reason: "prerequisite T-000040 is not done",
        eligible: false,
        reviewable: false,
        dependency_ready: false,
        updated_at: "2026-10-24T06:30:00Z"
      },
      {
        id: "T-000046",
        title: "Old dashboard layout",
        status: "cancelled",
        priority: "normal",
        assignee: "",
        labels: ["web-redesign"],
        claimed_by: null,
        claimed_at: null,
        blocked: false,
        blocked_reason: null,
        eligible: false,
        reviewable: false,
        dependency_ready: true,
        updated_at: "2026-10-18T12:00:00Z"
      },
      {
        id: "T-000047",
        title: "Export script test coverage",
        status: "done",
        priority: "normal",
        assignee: "agent-d",
        labels: ["tests"],
        claimed_by: null,
        claimed_at: null,
        blocked: false,
        blocked_reason: null,
        eligible: false,
        reviewable: false,
        dependency_ready: true,
        updated_at: "2026-10-19T09:30:00Z"
      },
      {
        id: "T-000048",
        title: "Markdown renderer for task notes",
        status: "backlog",
        priority: "normal",
        assignee: "",
        labels: ["web-redesign"],
        claimed_by: null,
        claimed_at: null,
        blocked: false,
        blocked_reason: null,
        eligible: false,
        reviewable: false,
        dependency_ready: true,
        updated_at: "2026-10-23T13:10:00Z"
      },
      {
        id: "T-000049",
        title: "Keyboard shortcut layer and help overlay",
        status: "ready",
        priority: "normal",
        assignee: "agent-c",
        labels: ["web-redesign", "accessibility"],
        claimed_by: null,
        claimed_at: null,
        blocked: false,
        blocked_reason: null,
        eligible: true,
        reviewable: false,
        dependency_ready: true,
        updated_at: "2026-10-23T17:45:00Z"
      },
      {
        id: "T-000050",
        title: "Retire the old refresh script",
        status: "done",
        priority: "low",
        assignee: "",
        labels: ["web-redesign"],
        claimed_by: null,
        claimed_at: null,
        blocked: false,
        blocked_reason: null,
        eligible: false,
        reviewable: false,
        dependency_ready: true,
        updated_at: "2026-10-21T10:00:00Z"
      },
      {
        id: "T-000051",
        title: "Remove the legacy export column from the API",
        status: "backlog",
        priority: "normal",
        assignee: "",
        labels: ["schema", "api"],
        claimed_by: null,
        claimed_at: null,
        blocked: true,
        blocked_reason: "prerequisite T-000043 is not done",
        eligible: false,
        reviewable: false,
        dependency_ready: false,
        updated_at: "2026-10-22T12:20:00Z"
      }
    ],
    edges: [
      { source: "T-000038", target: "T-000043" },
      { source: "T-000039", target: "T-000041" },
      { source: "T-000039", target: "T-000042" },
      { source: "T-000040", target: "T-000045" },
      { source: "T-000041", target: "T-000044" },
      { source: "T-000042", target: "T-000044" },
      { source: "T-000041", target: "T-000050" },
      { source: "T-000043", target: "T-000051" },
      { source: "T-000046", target: "T-000047" }
    ],
    notes: [
      {
        at: "2026-10-24T09:31:00Z",
        actor: "agent-a",
        text: "Read the owner brief and the current web layer. The graph already carries `claimed_by` but not `claimed_at`, so the design proposes one additive field on the shared projection.\n\nKey constraints carried into the design:\n\n- no inline scripts or style attributes\n- every response keeps the CSP and `nosniff`\n- never rely on colour alone\n\nSee the design system document for the measured contrast pairs."
      },
      {
        at: "2026-10-23T16:02:00Z",
        actor: "agent-a",
        text: "Drafted the status language: six shapes, six glyphs, six words. *Claimed* is a separate overlay so it can coexist with any status."
      }
    ],
    history: [
      {
        at: "2026-10-24T09:37:06Z",
        actor: "agent-a",
        kind: "status",
        payload: "ready → in_progress"
      },
      {
        at: "2026-10-24T09:37:06Z",
        actor: "agent-a",
        kind: "claim",
        payload: "claimed by agent-a"
      },
      {
        at: "2026-10-24T09:30:54Z",
        actor: "orchestrator",
        kind: "created",
        payload: "task created, priority high"
      },
      {
        at: "2026-10-24T09:28:00Z",
        actor: "orchestrator",
        kind: "dependency",
        payload: "T-000039 → T-000041"
      }
    ]
  };
})();
