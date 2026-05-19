# Contributing

Thanks for improving TCAD Process Simulator. The project is currently organized as a single-file application, so small, focused patches are much easier to review than broad rewrites.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run the app:

```bash
python tcad_simulator.py
```

Run headless smoke checks:

```bash
python tcad_simulator.py --mask-prompt-selftest
python tcad_simulator.py --webui-selftest --skip-video
```

Some deeper regression tests require local fixtures and may not be runnable by every contributor.

## Patch Guidelines

- Keep behavior changes narrowly scoped.
- Preserve the single-file entry point unless the issue explicitly concerns packaging or splitting.
- Do not commit runtime data, generated split artifacts, exported videos, private recipes, literature databases, or API keys.
- Keep optional dependencies optional. The base app should still start when optional PDF/GDS/video packages are absent.
- For model changes, document the numerical assumption or heuristic in the relevant code or docs.
- For WebUI changes, test both a fresh session and a restarted session when persistence is affected.

## Pull Request Checklist

- Describe what changed and why.
- Include the exact commands you ran.
- Mention any tests you could not run and why.
- Include screenshots or short recordings for visible UI changes when practical.
- Call out new dependencies, file formats, ports, environment variables, or license implications.

## Generated Split Package

`tcad_simulator_split/` and `tcad_simulator_split.zip` are generated artifacts. Do not include them in normal pull requests.
