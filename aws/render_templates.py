"""Render AWS example templates using an ignored private deployment JSON file."""
import argparse
import json
from pathlib import Path
from string import Template


def render(value, settings):
    if isinstance(value, str):
        return Template(value).substitute(settings)
    if isinstance(value, list):
        return [render(item, settings) for item in value]
    if isinstance(value, dict):
        return {key: render(item, settings) for key, item in value.items()}
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    settings = json.loads(args.config.read_text(encoding="utf-8"))
    if not isinstance(settings, dict) or any(not isinstance(value, str) or not value.strip()
                                            or "<" in value or "${" in value for value in settings.values()):
        parser.error("Use real private deployment values; example placeholders cannot be deployed")
    prepared = {}
    for path in Path(__file__).resolve().parent.glob("*.json"):
        if path.name.endswith(".example.json"):
            continue
        try:
            prepared[path.name] = render(json.loads(path.read_text(encoding="utf-8")), settings)
        except KeyError as error:
            parser.error("Private deployment configuration is missing " + str(error.args[0]))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in prepared.items():
        (args.output_dir / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print("Rendered", len(prepared), "AWS templates. Keep the output private and uncommitted.")


if __name__ == "__main__":
    main()
