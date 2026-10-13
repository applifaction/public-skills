# Public Pi Skills

A public collection of reusable [Pi](https://github.com/badlogic/pi-mono) skills for agent productivity, safe automation, and development workflows.

## Available skills

| Skill | Purpose |
| --- | --- |
| [`herdr-reload-all-pi-sessions`](skills/herdr-reload-all-pi-sessions/) | Reload every live Pi session through Herdr while allowing active turns to finish safely. |

## Installation

Install the repository as a Pi package from GitHub:

```bash
pi install git:github.com/applifaction/public-skills
```

Then reload Pi so newly installed skills become available:

```text
/reload
```

To use a local checkout instead:

```bash
git clone git@github.com:applifaction/public-skills.git
pi install ./public-skills
```

## Usage

Pi loads matching skills automatically. Invoke a skill explicitly when needed:

```text
/skill:herdr-reload-all-pi-sessions
```

### Reload all Pi sessions through Herdr

The skill first performs a dry run. After an explicit reload request, it:

- submits `/reload` immediately to `idle` and `done` Pi sessions;
- waits for `working` and `blocked` sessions to become safe before reloading them;
- uses bounded concurrency and `herdr agent prompt` instead of raw key injection;
- stores machine-readable results under `~/.local/state/herdr-reload-all-pi-sessions/`.

Requirements for this skill:

- Pi running inside Herdr;
- the `herdr` CLI on `PATH`;
- Python 3.10 or newer.

## Repository structure

```text
public-skills/
├── package.json
└── skills/
    └── herdr-reload-all-pi-sessions/
        ├── SKILL.md
        ├── agents/
        └── scripts/
```

Each directory under `skills/` is a portable Agent Skill and can contain its own scripts, references, and assets.
