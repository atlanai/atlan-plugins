// Written by tools/build.py from plugin.toml and command-hooks/telemetry_events.json: edit those files.

/** Where usage telemetry goes (nowhere while `write_key` is empty) and the names an event may carry. */
export const TELEMETRY = {
  "endpoint": "https://api.segment.io/v1/batch",
  "write_key": "",
  "plugin": "atlanai",
  "version": "0.5.66",
  "mcp_url": "https://api.atlan.com/mcp",
  "skills": [
    "atlanai:atlan-author-agents",
    "atlanai:atlan-author-knowledge",
    "atlanai:atlan-author-skills",
    "atlanai:atlan-connect-repo",
    "atlanai:atlan-content",
    "atlanai:atlan-continue",
    "atlanai:atlan-find-artifacts",
    "atlanai:atlan-get-started",
    "atlanai:atlan-manage-artifacts",
    "atlanai:atlan-share",
    "atlanai:atlan-troubleshoot",
    "atlanai:atlan-use-mcp-servers",
    "atlanai:atlan-use-skill",
    "atlanai-analytics:accuracy-check",
    "atlanai-analytics:analytics-ask",
    "atlanai-analytics:analytics-build",
    "atlanai-analytics:analytics-demo",
    "atlanai-analytics:eval-generation",
    "atlanai-analytics:metrics-generation",
    "atlanai-analytics:source-survey"
  ],
  "commands": [
    "atlanai:continue",
    "atlanai:feedback",
    "atlanai:get-started",
    "atlanai:share",
    "atlanai:use-skill"
  ]
} as const

/** Every event the hooks may send, and every property each may carry. */
export const SCHEMA = {
  "category": "claude_plugin",
  "enums": {
    "harness": [
      "claude_code",
      "codex",
      "cursor",
      "copilot",
      "unknown"
    ],
    "os": [
      "macos",
      "windows",
      "linux",
      "unknown"
    ],
    "surface": [
      "terminal",
      "desktop",
      "vscode",
      "mobile",
      "none"
    ],
    "operation": [
      "skill_create",
      "bulk_create",
      "output"
    ],
    "refusal": [
      "files-from",
      "symbolic-link",
      "credential-folder",
      "credential-file",
      "credential",
      "unsafe-path",
      "not-a-file",
      "too-many-files",
      "too-large",
      "missing-skill-md",
      "frontmatter",
      "not-text",
      "hidden-text",
      "local-batch",
      "receipt",
      "other"
    ],
    "runner": [
      "search_tools",
      "describe_tools",
      "call_tool",
      "call_read_tool",
      "call_write_tool",
      "other"
    ],
    "namespace": [
      "registry",
      "extension",
      "other"
    ],
    "failure": [
      "auth",
      "permission_denied",
      "not_found",
      "conflict",
      "rate_limited",
      "client_error",
      "server_error",
      "timeout",
      "unknown"
    ],
    "feedback_trigger": [
      "failure",
      "sampled"
    ],
    "flow": [
      "build",
      "demo",
      "dashboard"
    ],
    "stage": [
      "inspect",
      "scope",
      "build",
      "check",
      "preview",
      "publish",
      "ready"
    ],
    "stage_status": [
      "pending",
      "running",
      "waiting-for-user",
      "complete",
      "complete-with-held-items",
      "failed",
      "canceled"
    ],
    "intent_topic": [
      "domain",
      "purpose"
    ],
    "checks_choice": [
      "run",
      "skip"
    ],
    "dashboard_result": [
      "built",
      "declined"
    ],
    "run_mode": [
      "active",
      "paused",
      "local"
    ]
  },
  "events": {
    "install_active": {
      "about": "The plugin loaded in a session, at most once per UTC day for each install: the daily-active signal.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot"
      ],
      "props": {
        "first_seen": {
          "type": "boolean",
          "about": "True on the first report from this install in this harness."
        },
        "surface": {
          "type": "enum",
          "of": "surface",
          "optional": true,
          "about": "Where Claude Code draws."
        },
        "interactive": {
          "type": "boolean",
          "optional": true,
          "about": "Whether a person is at the prompt."
        }
      }
    },
    "skill_invoked": {
      "about": "The agent loaded one of this marketplace's own skills. A skill from anywhere else is never reported.",
      "hosts": [
        "claude_code"
      ],
      "props": {
        "skill": {
          "type": "listed",
          "of": "skills",
          "about": "The skill as `<plugin>:<skill>`, from the list this build ships."
        }
      }
    },
    "command_run": {
      "about": "A person ran one of this marketplace's own slash commands. Its arguments are never read.",
      "hosts": [
        "claude_code"
      ],
      "props": {
        "command": {
          "type": "listed",
          "of": "commands",
          "about": "The command as `<plugin>:<command>`, from the list this build ships."
        }
      }
    },
    "publish_prepared": {
      "about": "The publish helper checked a local skill folder or batch and sent the call on with its files.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot"
      ],
      "props": {
        "operation": {
          "type": "enum",
          "of": "operation"
        },
        "mcp_session_id": {
          "type": "mcp_session",
          "optional": true,
          "about": "The Atlan session the call was made in, as the gateway named it. The gateway keeps the same id with the call's user and organization, so this is the join to them; the hooks never see either."
        }
      }
    },
    "publish_refused": {
      "about": "The publish helper held a call back before it left the machine. The gateway never sees these.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot"
      ],
      "props": {
        "operation": {
          "type": "enum",
          "of": "operation"
        },
        "reason": {
          "type": "enum",
          "of": "refusal",
          "about": "The first check that failed, by its rule name."
        },
        "mcp_session_id": {
          "type": "mcp_session",
          "optional": true,
          "about": "The Atlan session the call was made in, as the gateway named it. The gateway keeps the same id with the call's user and organization, so this is the join to them; the hooks never see either."
        }
      }
    },
    "tool_call_failed": {
      "about": "An Atlan tool call came back as an error. The tool's arguments and the error's text are never sent.",
      "hosts": [
        "claude_code"
      ],
      "props": {
        "runner": {
          "type": "enum",
          "of": "runner",
          "about": "Which gateway helper tool was called."
        },
        "namespace": {
          "type": "enum",
          "of": "namespace",
          "about": "Whether the inner tool was the registry's, an Atlan app's, or anything else. The inner tool's name is never sent."
        },
        "reason": {
          "type": "enum",
          "of": "failure"
        },
        "mcp_session_id": {
          "type": "mcp_session",
          "optional": true,
          "about": "The Atlan session the call was made in, as the gateway named it. The gateway keeps the same id with the call's user and organization, so this is the join to them; the hooks never see either."
        }
      }
    },
    "update_prompted": {
      "about": "The agent was told a newer release of the plugin is out.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot"
      ],
      "props": {
        "from_version": {
          "type": "version"
        },
        "to_version": {
          "type": "version"
        },
        "forced": {
          "type": "boolean",
          "about": "Always false: the plugin never forces an update. Kept so the event matches the dashboard's."
        }
      }
    },
    "feedback_offered": {
      "about": "The agent was asked to offer the user a feedback draft. The draft itself is never sent by a hook.",
      "hosts": [
        "claude_code"
      ],
      "props": {
        "trigger": {
          "type": "enum",
          "of": "feedback_trigger"
        }
      }
    },
    "analytics_run_started": {
      "about": "An analytics run began: its first step was recorded.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot",
        "unknown"
      ],
      "props": {
        "flow": {
          "type": "enum",
          "of": "flow",
          "about": "Setup on your own data, the demo, or a dashboard made agentic."
        }
      }
    },
    "analytics_stage_reached": {
      "about": "An analytics run moved to a new stage, or to a new status of the one it is on. Repeats of the same stage and status are not sent. Counts only: never a metric's name, a source's name or a query.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot",
        "unknown"
      ],
      "props": {
        "stage": {
          "type": "enum",
          "of": "stage"
        },
        "status": {
          "type": "enum",
          "of": "stage_status"
        },
        "flow": {
          "type": "enum",
          "of": "flow"
        },
        "sources": {
          "type": "count",
          "about": "How many sources the run reads."
        },
        "metrics": {
          "type": "count",
          "about": "How many metrics the run holds."
        },
        "queries": {
          "type": "count",
          "about": "How many saved queries the run holds."
        },
        "checked": {
          "type": "count",
          "about": "How many of them passed their checks."
        },
        "held": {
          "type": "count",
          "about": "How many are held back by a check."
        },
        "released": {
          "type": "count",
          "about": "How many were saved to Atlan."
        }
      }
    },
    "analytics_flow_chosen": {
      "about": "The person chose the demo, or to make a dashboard agentic.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot",
        "unknown"
      ],
      "props": {
        "flow": {
          "type": "enum",
          "of": "flow"
        }
      }
    },
    "analytics_intent_recorded": {
      "about": "The person answered which business area to focus on, or what to accomplish in it. Which question was answered, never the answer.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot",
        "unknown"
      ],
      "props": {
        "topic": {
          "type": "enum",
          "of": "intent_topic"
        }
      }
    },
    "analytics_checks_chosen": {
      "about": "The person chose to run each metric and query on the warehouse, or to skip that.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot",
        "unknown"
      ],
      "props": {
        "choice": {
          "type": "enum",
          "of": "checks_choice"
        }
      }
    },
    "analytics_question_asked": {
      "about": "A chosen question was answered from the run's metrics. The question and its answer are never sent.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot",
        "unknown"
      ],
      "props": {
        "flow": {
          "type": "enum",
          "of": "flow"
        }
      }
    },
    "analytics_dashboard_finished": {
      "about": "The dashboard step ended: one was built, or the person declined.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot",
        "unknown"
      ],
      "props": {
        "result": {
          "type": "enum",
          "of": "dashboard_result"
        }
      }
    },
    "analytics_run_mode_changed": {
      "about": "The person paused the run, resumed it, or chose to keep the work local.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot",
        "unknown"
      ],
      "props": {
        "mode": {
          "type": "enum",
          "of": "run_mode"
        }
      }
    },
    "analytics_evals_generated": {
      "about": "An eval set was written. How many questions, never the questions.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot",
        "unknown"
      ],
      "props": {
        "questions": {
          "type": "count",
          "about": "How many questions the set holds."
        }
      }
    },
    "analytics_accuracy_scored": {
      "about": "An accuracy check was scored.",
      "hosts": [
        "claude_code",
        "codex",
        "cursor",
        "copilot",
        "unknown"
      ],
      "props": {
        "questions": {
          "type": "count",
          "about": "How many questions were scored."
        },
        "pass_percent": {
          "type": "count",
          "about": "The share answered correctly, 0 to 100."
        },
        "final": {
          "type": "boolean",
          "about": "Whether this was the final read, with the held-out questions."
        }
      }
    }
  }
} as const
