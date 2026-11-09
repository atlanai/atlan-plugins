// Written by tools/build.py from tools/tests/fixtures/publish-bundles.json: edit that file.

import type { Case } from './case'

export const CASES: readonly Case[] = [
  {
    "name": "clean",
    "about": "A clean bundle with CRLF line endings in one file",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "references/steps.md": "One.\r\nTwo.\r\n"
    },
    "expect": "send"
  },
  {
    "name": "skipped-entries",
    "about": ".git, __pycache__, node_modules and .DS_Store are skipped",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      ".git/HEAD": "ref: refs/heads/main\n",
      "__pycache__/x.pyc": "cache",
      "node_modules/a/index.js": "x",
      "references/.DS_Store": "x"
    },
    "expect": "send",
    "sent": [
      "SKILL.md"
    ]
  },
  {
    "name": "at-the-file-limit",
    "about": "Exactly 128 files",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n"
    },
    "generate": {
      "files": 127
    },
    "expect": "send"
  },
  {
    "name": "at-the-size-limit",
    "about": "Exactly 256 KiB in total",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n"
    },
    "generate": {
      "pad_to_bytes": 262144
    },
    "expect": "send"
  },
  {
    "name": "over-the-file-limit",
    "about": "129 files",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n"
    },
    "generate": {
      "files": 128
    },
    "expect": "refuse",
    "rules": [
      "too-many-files"
    ]
  },
  {
    "name": "over-the-size-limit",
    "about": "257 KiB in total",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n"
    },
    "generate": {
      "pad_to_bytes": 263168
    },
    "expect": "refuse",
    "rules": [
      "too-large"
    ]
  },
  {
    "name": "variation-selector-after-a-letter",
    "about": "U+FE0F after a plain letter, as in scripts/lint.py:39:52",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "scripts/lint.py": "x = 1\nprint(\"ok\ufe0f\")\n"
    },
    "expect": "refuse",
    "rules": [
      "variation-selector"
    ],
    "at": [
      [
        "scripts/lint.py",
        2,
        10
      ]
    ]
  },
  {
    "name": "direction-control",
    "about": "U+202E reverses the text a reader sees",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "notes.md": "safe \u202etxt.exe\n"
    },
    "expect": "refuse",
    "rules": [
      "direction-control"
    ],
    "at": [
      [
        "notes.md",
        1,
        6
      ]
    ]
  },
  {
    "name": "zero-width-space",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "notes.md": "rm\u200b -rf\n"
    },
    "expect": "refuse",
    "rules": [
      "zero-width"
    ],
    "at": [
      [
        "notes.md",
        1,
        3
      ]
    ]
  },
  {
    "name": "joiner-between-letters",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "notes.md": "a\u200db\n"
    },
    "expect": "refuse",
    "rules": [
      "zero-width-joiner"
    ],
    "at": [
      [
        "notes.md",
        1,
        2
      ]
    ]
  },
  {
    "name": "tag-characters-outside-a-flag",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "notes.md": "hi\udb40\udc69\udb40\udc67\udb40\udc6e\n"
    },
    "expect": "refuse",
    "rules": [
      "tag-character"
    ],
    "at": [
      [
        "notes.md",
        1,
        3
      ]
    ]
  },
  {
    "name": "c1-control",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "notes.md": "a\u0085b\n"
    },
    "expect": "refuse",
    "rules": [
      "control-character"
    ],
    "at": [
      [
        "notes.md",
        1,
        2
      ]
    ]
  },
  {
    "name": "emoji-sequences",
    "about": "A ZWJ emoji sequence, a keycap, a flag tag sequence and an emoji with U+FE0F are sent",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "notes.md": "\ud83d\udc68\u200d\ud83d\udcbb 1\ufe0f\u20e3 \ud83c\udff4\udb40\udc67\udb40\udc62\udb40\udc73\udb40\udc63\udb40\udc74\udb40\udc7f \u2708\ufe0f \ud83c\uddec\ud83c\udde7\n"
    },
    "expect": "send"
  },
  {
    "name": "symbolic-link",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n"
    },
    "links": {
      "other.md": "SKILL.md"
    },
    "expect": "refuse",
    "rules": [
      "symbolic-link"
    ]
  },
  {
    "name": "binary-file",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n"
    },
    "binary": {
      "assets/logo.png": "89504e470d0a1a0a0000"
    },
    "expect": "refuse",
    "rules": [
      "not-text"
    ]
  },
  {
    "name": "no-description",
    "files": {
      "SKILL.md": "---\nname: demo\n---\nBody.\n"
    },
    "expect": "refuse",
    "rules": [
      "frontmatter-description"
    ]
  },
  {
    "name": "no-frontmatter",
    "files": {
      "SKILL.md": "# Demo\n\nBody.\n"
    },
    "expect": "refuse",
    "rules": [
      "frontmatter"
    ]
  },
  {
    "name": "colon-in-a-plain-description",
    "about": "YAML refuses `: ` inside a plain value, and the registry's parser is real YAML",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when: asked to do it.\n---\nFollow the steps.\n"
    },
    "expect": "refuse",
    "rules": [
      "frontmatter-yaml"
    ],
    "at": [
      [
        "SKILL.md",
        3,
        13
      ]
    ]
  },
  {
    "name": "colon-in-a-quoted-or-folded-value",
    "about": "The same words quoted, or as a folded block, are valid YAML",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: >\n  Do a thing. Use when: asked to do it.\nlicense: \"MIT: see LICENSE\"\nallowed-tools:\n  - Bash(git add *)\n---\nFollow the steps.\n"
    },
    "expect": "send"
  },
  {
    "name": "unclosed-quote",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: \"Do a thing. Use when asked to do it.\n---\nFollow the steps.\n"
    },
    "expect": "refuse",
    "rules": [
      "frontmatter-yaml"
    ],
    "at": [
      [
        "SKILL.md",
        3,
        13
      ]
    ]
  },
  {
    "name": "duplicate-key",
    "about": "The registry refuses a key given twice rather than pick one",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\ndescription: Another.\n---\nFollow the steps.\n"
    },
    "expect": "refuse",
    "rules": [
      "frontmatter-yaml"
    ],
    "at": [
      [
        "SKILL.md",
        4,
        1
      ]
    ]
  },
  {
    "name": "tab-indent",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\nmetadata:\n\tlifecycle: draft\n---\nFollow the steps.\n"
    },
    "expect": "refuse",
    "rules": [
      "frontmatter-yaml"
    ],
    "at": [
      [
        "SKILL.md",
        5,
        1
      ]
    ]
  },
  {
    "name": "no-body",
    "about": "Frontmatter with nothing after it",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\n\n"
    },
    "expect": "refuse",
    "rules": [
      "no-body"
    ],
    "at": [
      [
        "SKILL.md",
        4,
        1
      ]
    ]
  },
  {
    "name": "no-root-skill-md",
    "files": {
      "demo/SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n"
    },
    "expect": "refuse",
    "rules": [
      "missing-skill-md"
    ]
  },
  {
    "name": "too-deep",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "a/b/c/d/e/f/g/h/i.md": "deep"
    },
    "expect": "refuse",
    "rules": [
      "unsafe-path"
    ]
  },
  {
    "name": "credential-string",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "config.md": "key: {{fake_aws_key}}\n"
    },
    "expect": "refuse",
    "rules": [
      "credential"
    ],
    "at": [
      [
        "config.md",
        1,
        6
      ]
    ]
  },
  {
    "name": "credential-file",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      ".env": "TOKEN=placeholder\n"
    },
    "expect": "refuse",
    "rules": [
      "credential-file"
    ]
  },
  {
    "name": "credential-file-configs",
    "about": "Package-registry and machine logins and Terraform variables are credential files",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      ".npmrc": "registry=https://registry.npmjs.org/\n",
      ".netrc": "machine example.com\n",
      "env/prod.tfvars": "region = \"eu-west-1\"\n"
    },
    "expect": "refuse",
    "rules": [
      "credential-file"
    ]
  },
  {
    "name": "credential-cloud-keys",
    "about": "A Google API key and a Stripe live secret key",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "config.md": "maps: AIza{{split}}SyA0123456789abcdefghijklmnopqrstuv\nbilling: sk_live{{split}}_0123456789abcdefghijklmn\n"
    },
    "expect": "refuse",
    "rules": [
      "credential"
    ],
    "at": [
      [
        "config.md",
        1,
        7
      ],
      [
        "config.md",
        2,
        10
      ]
    ]
  },
  {
    "name": "credential-passwords",
    "about": "A password set to a literal, and one inside a connection string",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "settings.ini": "db_pass{{split}}word = \"correcthorsebattery\"\nDB_URL=postgres{{split}}://admin:hunter2@db/app\n"
    },
    "expect": "refuse",
    "rules": [
      "credential"
    ],
    "at": [
      [
        "settings.ini",
        1,
        1
      ],
      [
        "settings.ini",
        2,
        8
      ]
    ]
  },
  {
    "name": "credential-near-misses",
    "about": "Placeholders, lookups and publishable keys are not secrets",
    "files": {
      "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n",
      "references/setup.md": "password = os.environ[\"DB_PASSWORD\"]\nPASSWORD=$DB_PASSWORD\npassword: \"<your password>\"\nclient_secret: SecretStr\nConnect with postgres://user:<password>@db/app or postgres://user:password@db/app.\nThe publishable key starts pk_live_ and is safe in a page.\n",
      "references/npmrc.md": "Never commit an .npmrc with a token.\n"
    },
    "expect": "send"
  },
  {
    "name": "files-and-files-from",
    "applies_to": [
      "hook"
    ],
    "about": "A skill entry with both files and files_from is refused before any read",
    "call": {
      "files": {
        "SKILL.md": "---\nname: demo\ndescription: Do a thing. Use when asked to do it.\n---\nFollow the steps.\n"
      },
      "files_from": "."
    },
    "expect": "refuse"
  },
  {
    "name": "outside-the-working-tree",
    "applies_to": [
      "hook"
    ],
    "about": "files_from names a folder outside the session's working directory and the harness's skill folders",
    "call": {
      "files_from": "/etc"
    },
    "expect": "refuse"
  },
  {
    "name": "credential-folder",
    "applies_to": [
      "hook"
    ],
    "about": "files_from names a credential folder inside the working tree",
    "call": {
      "files_from": ".ssh"
    },
    "expect": "refuse"
  }
]
